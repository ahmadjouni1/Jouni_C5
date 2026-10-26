"""Paper Tracker MCP server: three tools (Data, Action, Orchestration) and one resource.

The tools are thin wrappers over the core functions in arxiv_client.py and storage.py.
Run over stdio with:  uv run python -m server.main

stdout is the MCP stdio channel, so all logging goes to stderr.
"""

import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from pydantic import Field

from server.arxiv_client import ArxivUnavailableError, search_papers
from server.models import Paper, ReadingListResult, SavedNote, SkippedPaper
from server.storage import DuplicateNoteError, StorageError, list_notes, save_note

log = logging.getLogger("paper-mcp")
if not log.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[server] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

READING_LIST_MAX = 10

mcp = FastMCP(
    name="paper-tracker",
    instructions=(
        "arXiv paper tracker. Use search_arxiv to find papers, save_paper_note to save a note "
        "about one paper, and build_reading_list to search a topic and save the top papers in "
        "one step. Saved notes are readable at notes://papers."
    ),
    # Only ToolError messages reach the client; unexpected exceptions get a generic message,
    # never a raw traceback.
    mask_error_details=True,
)


@contextmanager
def _as_tool_error(tool: str) -> Iterator[None]:
    """Turn predictable core failures into a clean ToolError (and log it to stderr)."""
    try:
        yield
    except (ValueError, ArxivUnavailableError, DuplicateNoteError, StorageError) as exc:
        log.info("%s failed: %s", tool, exc)
        raise ToolError(str(exc)) from exc


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0] if parts else ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_arxiv(
    query: Annotated[str, Field(description="Search terms, e.g. 'LLM agent tool selection'")],
    max_results: Annotated[
        int, Field(description="How many papers to return (1-20)")
    ] = 5,
    sort_by: Annotated[
        Literal["relevance", "recent"],
        Field(description="'relevance' for best matches, 'recent' for newest submissions first"),
    ] = "relevance",
) -> list[Paper]:
    """Search arXiv for papers matching a query. Read-only: nothing is saved.

    Returns a list of papers (arxiv_id, title, authors, published date, short abstract, pdf_url).
    An empty list means arXiv had no matches.
    """
    log.info("received search_arxiv query=%r max_results=%s sort_by=%s", query, max_results, sort_by)
    with _as_tool_error("search_arxiv"):
        papers = await search_papers(query, max_results=max_results, sort_by=sort_by)
    log.info("search_arxiv -> %d paper(s): %s", len(papers), [p.arxiv_id for p in papers])
    return papers


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def save_paper_note(
    arxiv_id: Annotated[
        str,
        Field(description="arXiv ID from a search result, e.g. 2401.12345 (a vN suffix is stripped)"),
    ],
    title: Annotated[str, Field(description="The paper's title, as returned by search_arxiv")],
    note: Annotated[str, Field(description="Your note about the paper")],
    tags: Annotated[
        list[str] | None, Field(description="Optional tags, e.g. ['agents', 'to-read']")
    ] = None,
) -> SavedNote:
    """Save a note about one arXiv paper to local storage.

    Does not contact arXiv; pass the title from a search result. Each paper can be saved only
    once: saving an already-saved paper is an error, never a silent overwrite.
    """
    log.info("received save_paper_note arxiv_id=%r title=%r tags=%s", arxiv_id, title, tags)
    with _as_tool_error("save_paper_note"):
        saved = save_note(arxiv_id, title, note, tags)
    log.info("save_paper_note -> saved %s", saved.arxiv_id)
    return saved


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
async def build_reading_list(
    topic: Annotated[str, Field(description="Topic to build a reading list for")],
    n: Annotated[int, Field(description="How many top papers to save (1-10)")] = 3,
) -> ReadingListResult:
    """Build a reading list: search arXiv for a topic, then save a note for each of the top n papers.

    Papers that can't be saved (e.g. already saved) are listed under 'skipped' with a reason
    instead of failing the whole list.
    """
    log.info("received build_reading_list topic=%r n=%s", topic, n)
    if not topic or not topic.strip():
        raise ToolError("Topic cannot be empty.")
    if not 1 <= n <= READING_LIST_MAX:
        raise ToolError(f"n must be between 1 and {READING_LIST_MAX} (got {n}).")
    topic = topic.strip()

    # Step 1: search (same core function the Data tool uses).
    try:
        papers = await search_papers(topic, max_results=n, sort_by="relevance")
    except (ArxivUnavailableError, ValueError) as exc:
        log.info("build_reading_list failed at step 1 (search): %s", exc)
        raise ToolError(f"Step 1 (search arXiv) failed: {exc} Nothing was saved.") from exc
    if not papers:
        log.info("build_reading_list -> no papers found")
        return ReadingListResult(
            topic=topic, message=f"No arXiv papers found for '{topic}'. Nothing was saved."
        )

    # Step 2: save each paper (same core function the Action tool uses).
    tags = ["reading-list", _slug(topic)]
    saved: list[SavedNote] = []
    skipped: list[SkippedPaper] = []
    for paper in papers:
        note = f"Reading list: {topic}. {_first_sentence(paper.summary)}"
        try:
            saved.append(save_note(paper.arxiv_id, paper.title, note, tags))
        except (DuplicateNoteError, ValueError, StorageError) as exc:
            skipped.append(SkippedPaper(arxiv_id=paper.arxiv_id, reason=str(exc)))

    message = f"Saved {len(saved)} of {len(papers)} paper(s) for '{topic}'."
    if skipped:
        message += f" Skipped {len(skipped)} (see 'skipped' for reasons)."
    log.info("build_reading_list -> saved=%s skipped=%s",
             [s.arxiv_id for s in saved], [s.arxiv_id for s in skipped])
    return ReadingListResult(topic=topic, saved=saved, skipped=skipped, message=message)


@mcp.resource(
    "notes://papers",
    name="saved_paper_notes",
    description="All saved paper notes as a JSON list (read-only).",
    mime_type="application/json",
)
def saved_paper_notes() -> str:
    """All saved paper notes as a JSON list. Empty list if nothing has been saved."""
    log.info("received read notes://papers")
    try:
        notes = list_notes()
    except StorageError as exc:
        raise ResourceError(str(exc)) from exc
    return json.dumps([n.model_dump() for n in notes], indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(show_banner=False)
