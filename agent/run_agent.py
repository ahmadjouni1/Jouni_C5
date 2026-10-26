"""Paper Tracker agent: OpenAI Agents SDK as MCP Host/Client, Gemini via LiteLLM.

Starts the FastMCP server as a stdio subprocess, then runs an interactive chat loop.
Each tool call is printed so the full round trip is visible:

    [agent → MCP] calling search_arxiv({...})      (printed by the RunHooks below)
    [server] received search_arxiv ...             (printed by the server on stderr)
    [MCP → agent] 3 paper(s): ...                  (printed by the RunHooks below)

Run with:  uv run python -m agent.run_agent
"""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import litellm
from dotenv import load_dotenv
from openai.types.shared import Reasoning
from rich.console import Console
from rich.markdown import Markdown

from agents import (
    Agent,
    ModelRetryBackoffSettings,
    ModelRetrySettings,
    ModelSettings,
    RunHooks,
    Runner,
    retry_policies,
    set_tracing_disabled,
)
from agents.extensions.models.litellm_model import LitellmModel
from agents.mcp import MCPServerStdio

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Lite is the default: gemini-flash-latest's free tier allows only ~20 requests/day.
DEFAULT_MODEL = "gemini/gemini-flash-lite-latest"
ALTERNATE_MODEL = "gemini/gemini-flash-latest"
MAX_TURNS = 10

INSTRUCTIONS = """\
You are Paper Tracker, a concise assistant that helps a student find and track arXiv papers.

Tools:
- search_arxiv: find papers. Use sort_by="recent" when the user asks for recent, new or latest papers.
- save_paper_note: save a note about ONE paper. Pass its arxiv_id and title exactly as search_arxiv returned them.
- build_reading_list: when the user wants a reading list, i.e. to search a topic and save several papers in one step.

Rules:
- Never invent arXiv IDs. Only use IDs that came from search results in this conversation, or that the user typed.
- "The second one" etc. refers to the numbered list of papers you showed for the relevant search.
- Let the tools validate input: if the user types an ID, pass it as-is (use the ID as the title if you have
  no title for it) and report what the tool says. Don't reject it yourself.
- Every save request needs a save_paper_note call, even if you think the paper is already saved:
  the tool checks for duplicates, not you.
- Always end with a short reply to the user; never return an empty message.
- When a tool returns an error, explain it plainly in one or two sentences and suggest what to do next.
  Don't retry the same failing call.
- Show search results as a numbered list: title, first author, date, arXiv ID. Keep answers short.
- Only state facts the tools returned. build_reading_list returns titles and IDs (not authors or dates),
  so list those, plus any skipped papers with their reason.
"""

console = Console()

_transient_error = retry_policies.any(
    retry_policies.retry_after(),
    retry_policies.http_status([429, 500, 503]),
    retry_policies.network_error(),
)


async def _gemini_retry_policy(context) -> bool:
    """Retry per-minute rate limits and transient errors, but don't burn the free-tier quota.

    Failed requests count toward Gemini's daily cap, so a per-day 429 is never retried and an
    overloaded model (503) is retried only once.
    """
    if "PerDay" in str(context.error):
        return False
    if context.normalized.status_code == 503 and context.attempt > 1:
        return False
    return bool(await _transient_error(context))


# Gemini's free tier allows ~15 requests/min: back off and retry, honouring retry-after hints.
RETRY_SETTINGS = ModelRetrySettings(
    max_retries=3,
    backoff=ModelRetryBackoffSettings(initial_delay=5.0, max_delay=40.0, multiplier=2.0, jitter=True),
    policy=_gemini_retry_policy,
)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tool_output_text(result: Any) -> str:
    """Extract the text the MCP tool returned, whatever wrapper the SDK put around it."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return " ".join(_tool_output_text(item) for item in result)
    if isinstance(result, dict):
        return str(result.get("text", result))
    return str(getattr(result, "text", result))


def summarize_tool_result(result: Any) -> str:
    """One-line, human-readable summary of a tool result for the console."""
    text = _tool_output_text(result)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return _short(text)
    if isinstance(data, dict) and "result" in data and len(data) == 1:
        data = data["result"]
    if isinstance(data, list):
        ids = ", ".join(p.get("arxiv_id", "?") for p in data if isinstance(p, dict))
        return f"{len(data)} paper(s): {ids}" if data else "0 papers (empty list)"
    if isinstance(data, dict) and "saved" in data and "skipped" in data:
        saved = [s["arxiv_id"] for s in data["saved"]]
        skipped = [f"{s['arxiv_id']} ({s['reason']})" for s in data["skipped"]]
        return f"reading list '{data['topic']}': saved {saved}, skipped {skipped}"
    if isinstance(data, dict) and "saved_at" in data:
        return f"saved note for {data['arxiv_id']} ({_short(data['title'], 60)})"
    return _short(text)


class ToolCallLogger(RunHooks):
    """Prints each agent → MCP call and each MCP → agent result."""

    async def on_tool_start(self, context, agent, tool) -> None:
        args = getattr(context, "tool_arguments", None) or ""
        console.print(f"[bold cyan]\\[agent → MCP][/] calling [bold]{tool.name}[/]({args})")

    async def on_tool_end(self, context, agent, tool, result) -> None:
        summary = summarize_tool_result(result)
        is_error = "error" in summary.lower() or "failed" in summary.lower()
        color = "bold red" if is_error else "bold green"
        console.print(f"[{color}]\\[MCP → agent][/] {summary}")


def describe_model_error(exc: Exception) -> str:
    """Turn model/provider failures into a friendly message."""
    status = getattr(exc, "status_code", None)
    text = str(exc)
    model = os.environ.get("MODEL") or DEFAULT_MODEL
    other = ALTERNATE_MODEL if model == DEFAULT_MODEL else DEFAULT_MODEL
    if "PerDay" in text:
        return (
            f"Today's Gemini free-tier quota for {model} is used up (some models allow only ~20 "
            f"requests per day). Try again tomorrow, or set MODEL={other} in .env."
        )
    if status == 429 or "RESOURCE_EXHAUSTED" in text or isinstance(exc, litellm.RateLimitError):
        return (
            "Gemini's free tier is rate-limited (about 15 requests per minute, plus a daily cap). "
            "I retried a few times but it's still busy. Wait a minute and try again."
        )
    if status == 503 or isinstance(exc, litellm.ServiceUnavailableError):
        return (
            "Gemini is overloaded right now (HTTP 503), even after a retry. Try again shortly, "
            f"or set MODEL={other} in .env."
        )
    if status in (401, 403) or isinstance(exc, litellm.AuthenticationError):
        return "Gemini rejected the API key. Check GEMINI_API_KEY in your .env file."
    if status == 404 or isinstance(exc, litellm.NotFoundError):
        return f"The model '{model}' was not found. Check MODEL in .env."
    return f"The model request failed ({type(exc).__name__}): {_short(text, 300)}"


def _server_env() -> dict[str, str]:
    """Environment for the MCP server subprocess: inherit ours, minus the model key it doesn't need."""
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
    env["PYTHONUTF8"] = "1"
    return env


@asynccontextmanager
async def paper_tracker_agent() -> AsyncIterator[Agent]:
    """Start the MCP server over stdio and yield an Agent connected to it."""
    load_dotenv(PROJECT_ROOT / ".env")
    set_tracing_disabled(True)  # no OpenAI key; we log tool calls locally instead
    litellm.suppress_debug_info = True

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    model_name = os.environ.get("MODEL") or DEFAULT_MODEL

    async with MCPServerStdio(
        name="paper-tracker",
        params={
            "command": sys.executable,
            "args": ["-m", "server.main"],
            "cwd": str(PROJECT_ROOT),
            "env": _server_env(),
        },
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    ) as server:
        tools = await server.list_tools()
        console.print(
            f"[dim]Connected to MCP server 'paper-tracker' over stdio. "
            f"Tools: {', '.join(t.name for t in tools)}. Model: {model_name}[/]"
        )
        yield Agent(
            name="Paper Tracker",
            instructions=INSTRUCTIONS,
            model=LitellmModel(model=model_name, api_key=api_key),
            # Gemini 3 lite models default to minimal thinking, which led to wrong tool picks and
            # empty replies in testing; a little thinking fixes both.
            model_settings=ModelSettings(retry=RETRY_SETTINGS, reasoning=Reasoning(effort="low")),
            mcp_servers=[server],
        )


async def run_turn(agent: Agent, history: list, user_text: str) -> list:
    """Run one user turn, print the reply, and return the updated conversation history.

    On a model failure the friendly error is printed and the history is returned unchanged.
    """
    turn_input = [*history, {"role": "user", "content": user_text}]
    try:
        result = await Runner.run(agent, turn_input, hooks=ToolCallLogger(), max_turns=MAX_TURNS)
        called_tools = any(item.type == "tool_call_item" for item in result.new_items)
        if not str(result.final_output or "").strip() and not called_tools:
            # Gemini occasionally returns an empty reply; one nudged retry usually fixes it. Safe to
            # repeat because no tool ran.
            console.print("[dim](empty reply from the model, retrying once)[/]")
            nudge = {"role": "user", "content": f"{user_text}\n\n(Please handle this request now.)"}
            result = await Runner.run(
                agent, [*history, nudge], hooks=ToolCallLogger(), max_turns=MAX_TURNS
            )
    except Exception as exc:  # provider/model errors: show a friendly message, keep chatting
        console.print(f"[bold red]Paper Tracker:[/] {describe_model_error(exc)}")
        return history
    console.print("[bold magenta]Paper Tracker:[/]")
    console.print(Markdown(str(result.final_output)))
    return result.to_input_list()


async def chat() -> None:
    async with paper_tracker_agent() as agent:
        console.print("[bold]Paper Tracker[/] ready. Ask about arXiv papers; type 'exit' to quit.\n")
        history: list = []
        while True:
            try:
                user_text = (await asyncio.to_thread(input, "You: ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_text.lower() in {"exit", "quit"}:
                break
            if user_text:
                history = await run_turn(agent, history, user_text)
                console.print()
    console.print("[dim]Bye.[/]")


def main() -> None:
    try:
        asyncio.run(chat())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
