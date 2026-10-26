"""Core arXiv lookup: query the public Atom API and return clean Paper objects.

Plain Python (no MCP); the MCP layer in main.py converts the exceptions below into ToolErrors.
"""

import re
from typing import Literal

import feedparser
import httpx

from server.models import Paper

ARXIV_API_URL = "https://export.arxiv.org/api/query"
TIMEOUT_SECONDS = 10.0
MAX_RESULTS_LIMIT = 20
SUMMARY_MAX_CHARS = 400
USER_AGENT = "paper-mcp/0.1 (EECE 503P course project)"

_SORT_FIELDS = {"relevance": "relevance", "recent": "submittedDate"}
_VERSION_SUFFIX = re.compile(r"v\d+$")


class ArxivUnavailableError(Exception):
    """arXiv could not be reached or returned an unusable response."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"arXiv is unavailable right now ({reason}). Try again in a moment.")


def _clean(text: str) -> str:
    """Collapse runs of whitespace/newlines into single spaces."""
    return " ".join(text.split())


def _truncate(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def _arxiv_id_from_entry_id(entry_id: str) -> str:
    """'http://arxiv.org/abs/2401.12345v2' -> '2401.12345'; also handles 'hep-th/9901001v1'."""
    raw = entry_id.split("/abs/", 1)[-1]
    return _VERSION_SUFFIX.sub("", raw)


def _pdf_url(entry, arxiv_id: str) -> str:
    for link in entry.get("links", []):
        if link.get("type") == "application/pdf" or link.get("title") == "pdf":
            return link["href"]
    return f"https://arxiv.org/pdf/{arxiv_id}"


def _entry_to_paper(entry) -> Paper:
    arxiv_id = _arxiv_id_from_entry_id(entry.id)
    return Paper(
        arxiv_id=arxiv_id,
        title=_clean(entry.get("title", "")),
        authors=[_clean(a.get("name", "")) for a in entry.get("authors", []) if a.get("name")],
        published=entry.get("published", "")[:10],
        summary=_truncate(_clean(entry.get("summary", ""))),
        pdf_url=_pdf_url(entry, arxiv_id),
    )


async def search_papers(
    query: str,
    max_results: int = 5,
    sort_by: Literal["relevance", "recent"] = "relevance",
) -> list[Paper]:
    """Search arXiv and return up to `max_results` papers. An empty list means no matches.

    Raises ValueError for bad input and ArxivUnavailableError if arXiv can't be reached.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty.")
    if not 1 <= max_results <= MAX_RESULTS_LIMIT:
        raise ValueError(
            f"max_results must be between 1 and {MAX_RESULTS_LIMIT} (got {max_results})."
        )
    if sort_by not in _SORT_FIELDS:
        raise ValueError(f"sort_by must be 'relevance' or 'recent' (got {sort_by!r}).")

    params = {
        "search_query": f"all:{query.strip()}",
        "start": 0,
        "max_results": max_results,
        "sortBy": _SORT_FIELDS[sort_by],
        "sortOrder": "descending",
    }
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = await client.get(ARXIV_API_URL, params=params)
    except httpx.TimeoutException as exc:
        raise ArxivUnavailableError(f"request timed out after {TIMEOUT_SECONDS:.0f}s") from exc
    except httpx.HTTPError as exc:
        raise ArxivUnavailableError(f"connection error: {type(exc).__name__}") from exc

    if response.status_code == 429:
        raise ArxivUnavailableError("rate limited by arXiv, HTTP 429")
    if response.status_code != 200:
        raise ArxivUnavailableError(f"HTTP {response.status_code}")

    feed = feedparser.parse(response.text)
    if feed.bozo and not feed.entries:
        raise ArxivUnavailableError("unreadable response from the arXiv API")

    # arXiv reports query errors as a single entry whose id points at /api/errors.
    if feed.entries and "/api/errors" in feed.entries[0].get("id", ""):
        detail = _clean(feed.entries[0].get("summary", "unknown error"))
        raise ValueError(f"arXiv rejected the query: {detail}")

    return [_entry_to_paper(entry) for entry in feed.entries]
