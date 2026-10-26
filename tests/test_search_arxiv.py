"""search_arxiv (Data tool): happy path and predictable failures, with arXiv mocked."""

import httpx
import pytest

from server.arxiv_client import ARXIV_API_URL, build_search_query
from tests.conftest import error_text


async def test_happy_path_returns_clean_papers(client, arxiv_ok):
    result = await client.call_tool("search_arxiv", {"query": "tool selection", "max_results": 3})

    papers = result.structured_content["result"]
    assert [p["arxiv_id"] for p in papers] == ["2401.12345", "2403.00042", "hep-th/9901001"]

    first = papers[0]
    assert first["title"] == "Choosing the Right Tool: Selection Strategies for LLM Agents"
    assert first["authors"] == ["Alice Researcher", "Bob Scientist"]
    assert first["published"] == "2024-01-22"
    assert first["pdf_url"] == "http://arxiv.org/pdf/2401.12345v2"
    assert "\n" not in first["summary"]
    assert "  " not in first["summary"]
    assert len(first["summary"]) <= 403
    assert first["summary"].endswith("...")

    # Old-style entry without a PDF link gets a constructed URL.
    assert papers[2]["pdf_url"] == "https://arxiv.org/pdf/hep-th/9901001"

    params = arxiv_ok.calls.last.request.url.params
    assert params["search_query"] == "all:tool AND all:selection"
    assert params["max_results"] == "3"
    assert params["sortBy"] == "relevance"
    assert params["sortOrder"] == "descending"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("transformers", "all:transformers"),
        ("  Model  Context\nProtocol ", "all:Model AND all:Context AND all:Protocol"),
        ('"graph neural" (networks)', "all:graph AND all:neural AND all:networks"),
        ("search AND rescue", "all:search AND all:and AND all:rescue"),
    ],
)
def test_every_query_term_is_required(query, expected):
    # arXiv treats 'all:a b c' as a OR b OR c, so the client ANDs the terms.
    assert build_search_query(query) == expected


async def test_recent_sort_uses_submitted_date(client, arxiv_ok):
    await client.call_tool("search_arxiv", {"query": "agents", "sort_by": "recent"})
    assert arxiv_ok.calls.last.request.url.params["sortBy"] == "submittedDate"


@pytest.mark.parametrize("query", ["", "   ", "\n\t", '( "" )'])
async def test_empty_query_is_rejected(client, respx_mock, query):
    route = respx_mock.get(ARXIV_API_URL)
    message = await error_text(client, "search_arxiv", {"query": query})
    assert message == "Query cannot be empty."
    assert not route.called


@pytest.mark.parametrize("max_results", [0, 21, -3])
async def test_max_results_out_of_range(client, respx_mock, max_results):
    route = respx_mock.get(ARXIV_API_URL)
    message = await error_text(client, "search_arxiv", {"query": "x", "max_results": max_results})
    assert "between 1 and 20" in message
    assert not route.called


async def test_timeout_gives_clear_error(client, respx_mock):
    respx_mock.get(ARXIV_API_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    message = await error_text(client, "search_arxiv", {"query": "agents"})
    assert message.startswith("arXiv is unavailable right now (")
    assert "timed out" in message
    assert message.endswith("Try again in a moment.")


async def test_connection_error_gives_clear_error(client, respx_mock):
    respx_mock.get(ARXIV_API_URL).mock(side_effect=httpx.ConnectError("no route"))
    message = await error_text(client, "search_arxiv", {"query": "agents"})
    assert "arXiv is unavailable right now" in message
    assert "connection error" in message


async def test_server_error_gives_clear_error(client, respx_mock):
    respx_mock.get(ARXIV_API_URL).mock(return_value=httpx.Response(500, text="oops"))
    message = await error_text(client, "search_arxiv", {"query": "agents"})
    assert message == "arXiv is unavailable right now (HTTP 500). Try again in a moment."


async def test_rate_limit_gives_clear_error(client, respx_mock):
    respx_mock.get(ARXIV_API_URL).mock(return_value=httpx.Response(429))
    message = await error_text(client, "search_arxiv", {"query": "agents"})
    assert "rate limited by arXiv" in message


async def test_zero_results_is_an_empty_list_not_an_error(client, arxiv_empty):
    result = await client.call_tool("search_arxiv", {"query": "zzzz no such topic"})
    assert not result.is_error
    assert result.structured_content["result"] == []
