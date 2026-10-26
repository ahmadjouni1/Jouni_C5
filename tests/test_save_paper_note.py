"""save_paper_note (Action tool): saving, validation, duplicates, atomic storage."""

import json
from datetime import datetime

import pytest

from tests.conftest import error_text

NOTE = {"arxiv_id": "2401.12345", "title": "Choosing the Right Tool", "note": "Great survey."}


async def test_happy_path_saves_to_file(client, notes_file):
    result = await client.call_tool("save_paper_note", {**NOTE, "tags": ["agents", "tools"]})

    saved = result.structured_content
    assert saved["arxiv_id"] == "2401.12345"
    assert saved["title"] == "Choosing the Right Tool"
    assert saved["note"] == "Great survey."
    assert saved["tags"] == ["agents", "tools"]
    assert datetime.fromisoformat(saved["saved_at"]).tzinfo is not None

    on_disk = json.loads(notes_file.read_text(encoding="utf-8"))
    assert on_disk == [saved]


async def test_appends_without_touching_existing_notes(client, notes_file):
    await client.call_tool("save_paper_note", NOTE)
    await client.call_tool("save_paper_note", {**NOTE, "arxiv_id": "2403.00042", "title": "MCP"})
    ids = [n["arxiv_id"] for n in json.loads(notes_file.read_text(encoding="utf-8"))]
    assert ids == ["2401.12345", "2403.00042"]


@pytest.mark.parametrize("bad_id", ["abc123", "2401.123", "12345.6789", "arXiv 2401.12345", ""])
async def test_bad_id_shows_expected_format(client, notes_file, bad_id):
    message = await error_text(client, "save_paper_note", {**NOTE, "arxiv_id": bad_id})
    assert "Invalid arXiv ID" in message
    assert "2401.12345" in message
    assert "hep-th/9901001" in message
    assert not notes_file.exists()


async def test_duplicate_is_rejected_and_not_overwritten(client, notes_file):
    await client.call_tool("save_paper_note", NOTE)
    message = await error_text(client, "save_paper_note", {**NOTE, "note": "Overwrite attempt"})
    assert message == "Paper 2401.12345 is already saved."

    on_disk = json.loads(notes_file.read_text(encoding="utf-8"))
    assert len(on_disk) == 1
    assert on_disk[0]["note"] == "Great survey."


async def test_version_suffix_is_stripped(client):
    result = await client.call_tool("save_paper_note", {**NOTE, "arxiv_id": "2401.12345v2"})
    assert result.structured_content["arxiv_id"] == "2401.12345"

    # A different version of the same paper counts as a duplicate.
    message = await error_text(client, "save_paper_note", {**NOTE, "arxiv_id": "2401.12345v3"})
    assert message == "Paper 2401.12345 is already saved."


async def test_old_style_id_is_accepted(client):
    result = await client.call_tool(
        "save_paper_note", {**NOTE, "arxiv_id": "hep-th/9901001v1", "title": "Old paper"}
    )
    assert result.structured_content["arxiv_id"] == "hep-th/9901001"


@pytest.mark.parametrize("field", ["note", "title"])
async def test_empty_note_or_title_is_rejected(client, notes_file, field):
    message = await error_text(client, "save_paper_note", {**NOTE, field: "   "})
    assert message == f"{field.capitalize()} cannot be empty."
    assert not notes_file.exists()


async def test_tags_are_cleaned(client):
    result = await client.call_tool("save_paper_note", {**NOTE, "tags": [" a ", "a", "", "b"]})
    assert result.structured_content["tags"] == ["a", "b"]


async def test_write_leaves_no_temp_files(client, notes_file):
    await client.call_tool("save_paper_note", NOTE)
    assert [p.name for p in notes_file.parent.iterdir()] == ["notes.json"]
