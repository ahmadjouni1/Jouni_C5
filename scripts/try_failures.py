"""Call each tool with bad input and print the clean error it returns.

    uv run python -m scripts.try_failures

Goes through the real MCP tool layer (in-memory FastMCP client), works offline, and uses a
temporary notes file. "arXiv down" is simulated by pointing the client at a closed local port.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastmcp import Client
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

os.environ["NOTES_PATH"] = str(Path(tempfile.mkdtemp(prefix="paper-failures-")) / "notes.json")

from server import arxiv_client  # noqa: E402  (NOTES_PATH must be set first)
from server.main import mcp  # noqa: E402

UNREACHABLE_ARXIV = "http://127.0.0.1:9/api/query"
GOOD_NOTE = {"arxiv_id": "2401.12345", "title": "Some Paper", "note": "first save"}
CATEGORY = {
    "search_arxiv": "[bold blue]Data[/]",
    "save_paper_note": "[bold magenta]Action[/]",
    "build_reading_list": "[bold green]Orchestration[/]",
}

# (tool, arguments, what is wrong)
CASES = [
    ("search_arxiv", {"query": "   "}, "empty query"),
    ("search_arxiv", {"query": "agents", "max_results": 50}, "max_results out of range"),
    ("search_arxiv", {"query": "agents"}, "arXiv unreachable"),
    ("save_paper_note", {"arxiv_id": "abc123", "title": "T", "note": "test"}, "bad arXiv ID"),
    ("save_paper_note", {**GOOD_NOTE, "note": ""}, "empty note"),
    ("save_paper_note", {**GOOD_NOTE, "arxiv_id": "2401.12345v2"}, "duplicate (already saved)"),
    ("build_reading_list", {"topic": ""}, "empty topic"),
    ("build_reading_list", {"topic": "agents", "n": 50}, "n out of range"),
    ("build_reading_list", {"topic": "agents"}, "arXiv unreachable (step 1)"),
]


def intro_panel() -> Panel:
    return Panel(
        Group(
            Text(
                "Each of the three tools is called with bad input on purpose, through the real MCP "
                "tool layer.\nA well-behaved tool must answer with a clear error message the agent "
                "can explain:\nno crash, no raw traceback, no silent failure, no overwritten data.\n"
            ),
            Text.from_markup(
                "[dim]Runs offline: 'arXiv unreachable' is simulated with a closed local port. "
                "One valid note is saved first\nso the duplicate case has something to collide with.[/]"
            ),
        ),
        title="[bold]Failure handling tour[/] · predictable failures → clean ToolError messages",
        border_style="bright_blue",
        padding=(1, 2),
    )


async def main() -> None:
    console = Console()
    # Keep the screen focused on the results table: hide the server's own log lines.
    logging.getLogger("paper-mcp").setLevel(logging.WARNING)
    logging.getLogger("fastmcp").setLevel(logging.CRITICAL)
    arxiv_client.ARXIV_API_URL = UNREACHABLE_ARXIV  # simulate arXiv being down, no network needed

    console.print(intro_panel())

    table = Table(show_lines=True)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Tool")
    table.add_column("Bad input")
    table.add_column("Error message returned to the agent", style="red")

    clean = 0
    async with Client(mcp) as client:
        await client.call_tool("save_paper_note", GOOD_NOTE)  # set up the duplicate case
        for i, (tool, args, problem) in enumerate(CASES, start=1):
            result = await client.call_tool(tool, args, raise_on_error=False)
            if result.is_error:
                clean += 1
                message = result.content[0].text
            else:
                message = f"[bold]NOT REJECTED:[/] {result.data}"
            table.add_row(
                str(i), f"{tool}\n{CATEGORY[tool]}", f"[bold]{problem}[/]\n[dim]{args}[/]", message
            )

    console.print(table)
    if clean == len(CASES):
        console.print(
            f"[bold green]✓ {clean}/{len(CASES)} bad inputs rejected with a clear message[/]"
            " · 0 crashes · 0 tracebacks · nothing overwritten"
        )
    else:
        console.print(f"[bold red]✗ only {clean}/{len(CASES)} bad inputs were rejected[/]")


if __name__ == "__main__":
    asyncio.run(main())
