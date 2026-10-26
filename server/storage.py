"""Core note storage: a JSON file of SavedNote records, written atomically.

Plain Python (no MCP); the MCP layer in main.py converts the exceptions below into ToolErrors.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from server.models import SavedNote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOTES_PATH = PROJECT_ROOT / "data" / "notes.json"

_NEW_STYLE_ID = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_OLD_STYLE_ID = re.compile(r"^[a-z\-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$")
_VERSION_SUFFIX = re.compile(r"v\d+$")


class DuplicateNoteError(Exception):
    """A note for this paper already exists."""


class StorageError(Exception):
    """The notes file could not be read or written."""


def notes_path() -> Path:
    """Location of the notes file; override with the NOTES_PATH env var."""
    return Path(os.environ.get("NOTES_PATH", DEFAULT_NOTES_PATH))


def normalize_arxiv_id(arxiv_id: str) -> str:
    """Validate an arXiv ID and strip any version suffix ('2401.12345v2' -> '2401.12345')."""
    candidate = (arxiv_id or "").strip()
    if not (_NEW_STYLE_ID.match(candidate) or _OLD_STYLE_ID.match(candidate)):
        raise ValueError(
            f"Invalid arXiv ID {arxiv_id!r}. Expected a new-style ID like 2401.12345 "
            "(optionally with a version, e.g. 2401.12345v2) or an old-style ID like hep-th/9901001."
        )
    return _VERSION_SUFFIX.sub("", candidate)


def list_notes() -> list[SavedNote]:
    """Return all saved notes, or an empty list if nothing has been saved yet."""
    path = notes_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        return [SavedNote.model_validate(item) for item in raw]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise StorageError(f"Notes file {path} is corrupted and could not be read.") from exc
    except OSError as exc:
        raise StorageError(f"Notes file {path} could not be read ({exc.strerror}).") from exc


def _write_notes_atomically(notes: list[SavedNote]) -> None:
    path = notes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([n.model_dump() for n in notes], indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".notes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
        os.replace(tmp_name, path)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise StorageError(f"Notes file {path} could not be written ({exc.strerror}).") from exc


def _clean_tags(tags: list[str] | None) -> list[str]:
    seen: list[str] = []
    for tag in tags or []:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def save_note(arxiv_id: str, title: str, note: str, tags: list[str] | None = None) -> SavedNote:
    """Append a note about one paper. Never overwrites an existing note.

    Raises ValueError for bad input, DuplicateNoteError if the paper is already saved,
    and StorageError if the notes file can't be read or written.
    """
    clean_id = normalize_arxiv_id(arxiv_id)
    if not title or not title.strip():
        raise ValueError("Title cannot be empty.")
    if not note or not note.strip():
        raise ValueError("Note cannot be empty.")

    notes = list_notes()
    if any(existing.arxiv_id == clean_id for existing in notes):
        raise DuplicateNoteError(f"Paper {clean_id} is already saved.")

    saved = SavedNote(
        arxiv_id=clean_id,
        title=" ".join(title.split()),
        note=note.strip(),
        tags=_clean_tags(tags),
        saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    _write_notes_atomically([*notes, saved])
    return saved
