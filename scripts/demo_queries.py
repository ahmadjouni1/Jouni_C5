"""Scripted screencast demo: runs the five demo queries through the agent, in order.

    uv run python -m scripts.demo_queries              # fresh temporary notes file (repeatable)
    uv run python -m scripts.demo_queries --pause 15   # longer reading time between steps
    uv run python -m scripts.demo_queries --step       # press Enter to advance each step
    uv run python -m scripts.demo_queries --keep       # use your real notes file (data/notes.json)

The screen explains itself (no narration needed): an intro with the three tools and a legend
for the round-trip lines, a caption before each query, and a closing summary with the saved
notes (read from the notes://papers resource), the challenges, and the repo link.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import NamedTuple

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from agent.run_agent import console, paper_tracker_agent, run_turn

REPO_URL = "https://github.com/ahmadjouni1/Jouni_C5"


class DemoStep(NamedTuple):
    query: str
    shows: str  # what this step demonstrates
    watch: str  # what the viewer should look for


DEMO_STEPS = [
    DemoStep(
        "Find 3 recent papers on LLM agent tool selection",
        "DATA tool · search_arxiv",
        "A read-only lookup on arXiv. Nothing is saved.",
    ),
    DemoStep(
        "Save the second one with the note: relevant for my 503 project",
        "ACTION tool · save_paper_note",
        "The agent remembers the list, picks paper #2, and writes one note to local storage.",
    ),
    DemoStep(
        "Build me a reading list of 3 papers on Model Context Protocol security",
        "ORCHESTRATION tool · build_reading_list",
        "One tool call that searches arXiv, then saves the top 3 papers.",
    ),
    DemoStep(
        "Save paper abc123 with note test",
        "FAILURE case · invalid arXiv ID",
        "The server rejects 'abc123' with a clear error (red). Nothing is saved, nothing crashes.",
    ),
    DemoStep(
        "Save the second paper from the first search again",
        "FAILURE case · duplicate save",
        "That paper is already saved: a clear error instead of a silent overwrite.",
    ),
]
DEMO_QUERIES = [step.query for step in DEMO_STEPS]

CHALLENGES = [
    ("Keeping tools single-purpose",
     "Rule: if a description needs 'and', split it. The save tool takes the title as input, "
     "so it never calls arXiv; the 'search AND save' job became the orchestration tool."),
    ("Handling failures predictably",
     "Every bad input becomes a clear ToolError the agent can explain; duplicates are never "
     "overwritten (see scripts/try_failures.py)."),
    ("Off-topic arXiv results",
     "arXiv OR-ed the words of a query (Bitcoin papers for 'MCP security'). "
     "Fix: every search term is now required."),
    ("Gemini free-tier limits",
     "20 requests/day and 503s on flash. Fix: flash-lite, quota-aware retries, pauses between queries."),
]


def intro_panel() -> Panel:
    tools = Table(box=None, padding=(0, 2), show_header=True, header_style="bold")
    tools.add_column("Category")
    tools.add_column("Tool")
    tools.add_column("What it does")
    tools.add_row("[bold blue]Data[/]", "search_arxiv", "Looks up papers on arXiv (read-only)")
    tools.add_row("[bold magenta]Action[/]", "save_paper_note", "Saves a note about one paper to notes.json")
    tools.add_row("[bold green]Orchestration[/]", "build_reading_list", "Searches a topic, then saves the top N papers")

    flow = Text.from_markup(
        "You  →  [bold]Agent[/] (OpenAI Agents SDK = MCP host/client, Gemini via LiteLLM)\n"
        "     ⇄  stdio (MCP)  ⇄  [bold]FastMCP server[/]  →  arXiv API  /  notes.json"
    )

    legend = Table(box=None, padding=(0, 2), show_header=False)
    legend.add_row("[bold cyan]\\[agent → MCP][/]", "the agent sends a tool call to the MCP server")
    legend.add_row("[yellow]\\[server][/]", "the FastMCP server receives the call and runs the tool")
    legend.add_row("[bold green]\\[MCP → agent][/]", "the result travels back to the agent ([bold red]red[/] = clean error)")
    legend.add_row("[bold magenta]Paper Tracker:[/]", "the agent's answer to the user")

    return Panel(
        Group(
            Text("An arXiv paper tracker built as an MCP toolkit: exactly three tools, one per category.\n"),
            tools,
            Text("\nArchitecture", style="bold"),
            flow,
            Text("\nHow to read the output", style="bold"),
            legend,
        ),
        title="[bold]Paper Tracker MCP Toolkit[/] · EECE 503P C5 Agent Tools",
        border_style="bright_blue",
        padding=(1, 2),
    )


def step_panel(i: int, step: DemoStep) -> Panel:
    return Panel(
        Text.from_markup(f"[bold]Watch for:[/] {step.watch}"),
        title=f"[bold]Step {i}/{len(DEMO_STEPS)} · {step.shows}[/]",
        title_align="left",
        border_style="bright_blue",
    )


def notes_table(notes: list[dict]) -> Table:
    table = Table(title="Saved notes, read from the notes://papers resource", title_style="bold")
    table.add_column("arXiv ID", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Tags", style="dim")
    for note in notes:
        table.add_row(note["arxiv_id"], note["title"], ", ".join(note["tags"]) or "-")
    return table


def outro_panel() -> Panel:
    rows = Table(box=None, padding=(0, 2), show_header=False)
    for title, text in CHALLENGES:
        rows.add_row(f"[bold]{title}[/]", text)
    return Panel(
        Group(
            rows,
            Text.from_markup(
                f"\n[bold]Code:[/] [link={REPO_URL}]{REPO_URL}[/link]\n"
                "[bold]Next:[/] uv run python -m scripts.try_failures  (every tool, bad input on purpose)"
            ),
        ),
        title="Challenges and how I solved them",
        border_style="bright_blue",
        padding=(1, 2),
    )


async def wait(seconds: float, step_mode: bool, label: str) -> None:
    if step_mode:
        await asyncio.to_thread(input, f"  (press Enter for {label}) ")
    else:
        console.print(f"[dim]  {label} in {seconds:.0f}s ...[/]")
        await asyncio.sleep(seconds)


async def run_demo(pause_seconds: float, step_mode: bool) -> None:
    console.clear()
    console.print(intro_panel())
    await wait(max(pause_seconds, 10), step_mode, "the demo")

    async with paper_tracker_agent() as agent:
        history: list = []
        for i, step in enumerate(DEMO_STEPS, start=1):
            console.print()
            console.print(step_panel(i, step))
            console.print(f"[bold]You:[/] {step.query}")
            history = await run_turn(agent, history, step.query)
            if i < len(DEMO_STEPS):
                # Also respects Gemini's free-tier rate limit and arXiv's request etiquette.
                await wait(pause_seconds, step_mode, f"step {i + 1}")

        await wait(pause_seconds, step_mode, "the summary")
        console.print()
        console.print(Rule("[bold]Summary[/]"))
        result = await agent.mcp_servers[0].read_resource("notes://papers")
        console.print(notes_table(json.loads(result.contents[0].text)))

    console.print()
    console.print(outro_panel())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="use the real notes file instead of a temp one")
    parser.add_argument("--pause", type=float, default=8.0, help="seconds to wait between steps")
    parser.add_argument("--step", action="store_true", help="press Enter to advance instead of timed pauses")
    args = parser.parse_args()

    if not args.keep:
        notes = Path(tempfile.mkdtemp(prefix="paper-demo-")) / "notes.json"
        os.environ["NOTES_PATH"] = str(notes)  # inherited by the MCP server subprocess
    asyncio.run(run_demo(args.pause, args.step))


if __name__ == "__main__":
    main()
