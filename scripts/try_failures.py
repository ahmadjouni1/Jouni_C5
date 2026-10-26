"""Call each tool with bad input and print the clean error it returns.

    uv run python -m scripts.try_failures

Goes through the real MCP tool layer (in-memory FastMCP client), works offline, and uses a
temporary notes file. "arXiv down" is simulated by pointing the client at a closed local port.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from fastmcp import Client
from rich.console import Console
from rich.table import Table

os.environ["NOTES_PATH"] = str(Path(tempfile.mkdtemp(prefix="paper-failures-")) / "notes.json")

from server import arxiv_client  # noqa: E402  (NOTES_PATH must be set first)
from server.main import mcp  # noqa: E402

UNREACHABLE_ARXIV = "http://127.0.0.1:9/api/query"
GOOD_NOTE = {"arxiv_id": "2401.12345", "title": "Some Paper", "note": "first save"}

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


async def main() -> None:
    console = Console()
    arxiv_client.ARXIV_API_URL = UNREACHABLE_ARXIV  # simulate arXiv being down, no network needed

    table = Table(title="Predictable failures → clean ToolError messages", show_lines=True)
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Bad input")
    table.add_column("Error returned to the agent", style="red")

    async with Client(mcp) as client:
        await client.call_tool("save_paper_note", GOOD_NOTE)  # set up the duplicate case
        for tool, args, problem in CASES:
            result = await client.call_tool(tool, args, raise_on_error=False)
            message = result.content[0].text if result.is_error else f"(no error!) {result.data}"
            table.add_row(tool, f"{problem}\n[dim]{args}[/]", message)

    console.print(table)


if __name__ == "__main__":
    asyncio.run(main())
