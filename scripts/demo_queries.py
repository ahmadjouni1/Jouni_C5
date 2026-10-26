"""Scripted screencast demo: runs the five demo queries through the agent, in order.

    uv run python -m scripts.demo_queries            # fresh temporary notes file (repeatable)
    uv run python -m scripts.demo_queries --keep     # use your real notes file (data/notes.json)

Queries 4 and 5 are deliberate failures (bad arXiv ID, duplicate save). The run ends by
reading the notes://papers resource over the same MCP connection.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from rich.rule import Rule

from agent.run_agent import console, paper_tracker_agent, run_turn

DEMO_QUERIES = [
    "Find 3 recent papers on LLM agent tool selection",
    "Save the second one with the note: relevant for my 503 project",
    "Build me a reading list of 3 papers on Model Context Protocol security",
    "Save paper abc123 with note test",
    "Save the second paper from the first search again",
]


async def run_demo(pause_seconds: float) -> None:
    async with paper_tracker_agent() as agent:
        history: list = []
        for i, query in enumerate(DEMO_QUERIES, start=1):
            if i > 1:
                # Gemini's free tier is ~15 requests/min; arXiv asks for ~3s between calls.
                console.print(f"[dim](pausing {pause_seconds:.0f}s for rate limits)[/]")
                await asyncio.sleep(pause_seconds)
            console.print(Rule(f"Demo query {i}/{len(DEMO_QUERIES)}"))
            console.print(f"[bold]You:[/] {query}")
            history = await run_turn(agent, history, query)

        console.print(Rule("Resource notes://papers"))
        result = await agent.mcp_servers[0].read_resource("notes://papers")
        notes = json.loads(result.contents[0].text)
        console.print(f"{len(notes)} saved note(s):")
        for note in notes:
            console.print(f"  • {note['arxiv_id']}  {note['title'][:70]}  [dim]{note['tags']}[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="use the real notes file instead of a temp one")
    parser.add_argument("--pause", type=float, default=8.0, help="seconds to wait between queries")
    args = parser.parse_args()

    if not args.keep:
        notes = Path(tempfile.mkdtemp(prefix="paper-demo-")) / "notes.json"
        os.environ["NOTES_PATH"] = str(notes)  # inherited by the MCP server subprocess
        console.print(f"[dim]Using a fresh notes file for this demo: {notes}[/]")
    asyncio.run(run_demo(args.pause))


if __name__ == "__main__":
    main()
