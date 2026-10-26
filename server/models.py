"""Pydantic models shared by the core functions and the MCP tools."""

from pydantic import BaseModel, Field


class Paper(BaseModel):
    """One arXiv search result."""

    arxiv_id: str = Field(description="arXiv identifier without version suffix, e.g. 2401.12345")
    title: str
    authors: list[str]
    published: str = Field(description="Publication date, YYYY-MM-DD")
    summary: str = Field(description="Abstract, trimmed to about 400 characters")
    pdf_url: str


class SavedNote(BaseModel):
    """A note about a paper, as stored in the local notes file."""

    arxiv_id: str
    title: str
    note: str
    tags: list[str] = Field(default_factory=list)
    saved_at: str = Field(description="ISO 8601 timestamp (UTC) of when the note was saved")


class SkippedPaper(BaseModel):
    """A paper the reading-list builder found but could not save."""

    arxiv_id: str
    reason: str


class ReadingListResult(BaseModel):
    """Outcome of building a reading list: what was saved, what was skipped, and why."""

    topic: str
    saved: list[SavedNote] = Field(default_factory=list)
    skipped: list[SkippedPaper] = Field(default_factory=list)
    message: str = Field(description="Human-readable summary of the outcome")
