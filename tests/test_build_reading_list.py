"""build_reading_list (Orchestration tool): search + save, partial failures, step errors."""

import json

import httpx
import pytest

from server.arxiv_client import ARXIV_API_URL
from tests.conftest import error_text

TOPIC = "Model Context Protocol security"


async def test_happy_path_saves_top_papers(client, arxiv_ok, notes_file):
    result = await client.call_tool("build_reading_list", {"topic": TOPIC, "n": 3})
    data = result.structured_content

    assert data["topic"] == TOPIC
    assert [s["arxiv_id"] for s in data["saved"]] == ["2401.12345", "2403.00042", "hep-th/9901001"]
    assert data["skipped"] == []
    assert data["message"] == f"Saved 3 of 3 paper(s) for '{TOPIC}'."

    first = data["saved"][0]
    assert first["note"] == (
        f"Reading list: {TOPIC}. Large language model agents increasingly rely on external tools."
    )
    assert first["tags"] == ["reading-list", "model-context-protocol-security"]
    assert data["saved"][2]["note"].endswith("old-style arXiv identifier!")

    params = arxiv_ok.calls.last.request.url.params
    assert params["max_results"] == "3"
    assert params["sortBy"] == "relevance"
    assert len(json.loads(notes_file.read_text(encoding="utf-8"))) == 3


async def test_already_saved_paper_goes_to_skipped(client, arxiv_ok, notes_file):
    await client.call_tool(
        "save_paper_note",
        {"arxiv_id": "2403.00042", "title": "Securing the Model Context Protocol", "note": "mine"},
    )

    result = await client.call_tool("build_reading_list", {"topic": TOPIC, "n": 3})
    data = result.structured_content

    assert not result.is_error
    assert [s["arxiv_id"] for s in data["saved"]] == ["2401.12345", "hep-th/9901001"]
    assert data["skipped"] == [
        {"arxiv_id": "2403.00042", "reason": "Paper 2403.00042 is already saved."}
    ]
    assert "Skipped 1" in data["message"]

    # The user's own note was not overwritten.
    notes = {n["arxiv_id"]: n for n in json.loads(notes_file.read_text(encoding="utf-8"))}
    assert notes["2403.00042"]["note"] == "mine"


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"return_value": httpx.Response(500)},
        {"side_effect": httpx.ReadTimeout("slow")},
    ],
    ids=["http-500", "timeout"],
)
async def test_search_failure_names_the_step(client, respx_mock, notes_file, mock_kwargs):
    respx_mock.get(ARXIV_API_URL).mock(**mock_kwargs)
    message = await error_text(client, "build_reading_list", {"topic": TOPIC})
    assert message.startswith("Step 1 (search arXiv) failed: arXiv is unavailable right now")
    assert message.endswith("Nothing was saved.")
    assert not notes_file.exists()


@pytest.mark.parametrize("topic", ["", "   "])
async def test_empty_topic_is_rejected(client, respx_mock, topic):
    route = respx_mock.get(ARXIV_API_URL)
    message = await error_text(client, "build_reading_list", {"topic": topic})
    assert message == "Topic cannot be empty."
    assert not route.called


@pytest.mark.parametrize("n", [0, 11])
async def test_n_out_of_range_is_rejected(client, respx_mock, n):
    route = respx_mock.get(ARXIV_API_URL)
    message = await error_text(client, "build_reading_list", {"topic": TOPIC, "n": n})
    assert "between 1 and 10" in message
    assert not route.called


async def test_zero_results_is_empty_result_with_message(client, arxiv_empty, notes_file):
    result = await client.call_tool("build_reading_list", {"topic": "zzzz no such topic"})
    data = result.structured_content

    assert not result.is_error
    assert data["saved"] == []
    assert data["skipped"] == []
    assert "No arXiv papers found" in data["message"]
    assert not notes_file.exists()
