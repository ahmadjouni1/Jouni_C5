"""notes://papers resource: read-only JSON view of saved notes."""

import json

import pytest

URI = "notes://papers"


async def read_notes(client) -> list[dict]:
    contents = await client.read_resource(URI)
    assert contents[0].mime_type == "application/json"
    return json.loads(contents[0].text)


async def test_resource_is_listed(client):
    resources = await client.list_resources()
    assert URI in [str(r.uri) for r in resources]


async def test_empty_when_no_file(client, notes_file):
    assert not notes_file.exists()
    assert await read_notes(client) == []


async def test_populated_after_saves(client):
    for arxiv_id, title in [("2401.12345", "First"), ("2403.00042", "Second")]:
        await client.call_tool(
            "save_paper_note", {"arxiv_id": arxiv_id, "title": title, "note": "n", "tags": ["t"]}
        )

    notes = await read_notes(client)
    assert [(n["arxiv_id"], n["title"], n["tags"]) for n in notes] == [
        ("2401.12345", "First", ["t"]),
        ("2403.00042", "Second", ["t"]),
    ]
    assert all("saved_at" in n for n in notes)


async def test_corrupted_file_gives_clear_error(client, notes_file):
    notes_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception, match="corrupted"):
        await client.read_resource(URI)
