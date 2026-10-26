"""Shared fixtures: in-memory MCP client, isolated notes file, and mocked arXiv responses."""

from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from server.arxiv_client import ARXIV_API_URL
from server.main import mcp

FIXTURES = Path(__file__).parent / "fixtures"

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title type="html">ArXiv Query: search_query=all:nothing</title>
  <id>http://arxiv.org/api/empty</id>
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>
"""


@pytest.fixture(autouse=True)
def notes_file(tmp_path, monkeypatch) -> Path:
    """Point storage at a per-test temp file so tests never touch data/notes.json."""
    path = tmp_path / "notes.json"
    monkeypatch.setenv("NOTES_PATH", str(path))
    return path


@pytest.fixture
async def client():
    """In-memory FastMCP client: calls go through the real MCP tool layer."""
    async with Client(mcp) as c:
        yield c


@pytest.fixture
def sample_feed() -> str:
    return (FIXTURES / "arxiv_sample.xml").read_text(encoding="utf-8")


@pytest.fixture
def arxiv_ok(respx_mock, sample_feed):
    """arXiv returns the 3-entry sample feed."""
    return respx_mock.get(ARXIV_API_URL).mock(return_value=httpx.Response(200, text=sample_feed))


@pytest.fixture
def arxiv_empty(respx_mock):
    """arXiv returns a valid feed with no entries."""
    return respx_mock.get(ARXIV_API_URL).mock(return_value=httpx.Response(200, text=EMPTY_FEED))


async def error_text(client: Client, tool: str, args: dict) -> str:
    """Call a tool that should fail and return its error message."""
    result = await client.call_tool(tool, args, raise_on_error=False)
    assert result.is_error, f"{tool} was expected to fail but returned {result.structured_content}"
    return result.content[0].text
