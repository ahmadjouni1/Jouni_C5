# Reflection notes (raw material, not the final reflection)

## Where the line between the three tools was drawn, and why

- Rule used: if a tool's description needs an "and", split it and let the orchestration tool do the "and".
- `search_arxiv` (Data): read-only, no side effects. Safe to call speculatively; the agent can search
  several times before deciding what to save.
- `save_paper_note` (Action): the only tool that writes. It takes the title as an argument instead of
  fetching it from arXiv:
  - that keeps it single-purpose, with no network dependency, so it fails only for storage or input
    reasons
  - the tradeoff is that the agent must pass the title correctly (it gets it from search results)
- `build_reading_list` (Orchestration): exists because "search a topic **and** save the top N" is a
  common higher-level request.
  - It reuses the core functions (`search_papers`, `save_note`) directly instead of calling the other
    tools over MCP: one source of truth for validation, no server → client → server loop.
  - Partial failure is handled per paper (`skipped` with a reason) rather than all-or-nothing. A
    duplicate shouldn't kill the whole list.
  - Errors name the step that failed ("Step 1 (search arXiv) failed: ...").
- Listing saved notes is a pure parameterless read, so it became a resource (`notes://papers`)
  instead of a 4th tool.
- Logic lives in plain Python (`arxiv_client.py`, `storage.py`); the MCP tools are thin wrappers.
  This made testing easy and kept MCP concerns (ToolError, logging) in one file.

## Did three tools feel right?

- For the core loop (find, save, batch-save) yes: the agent never seemed to be missing a tool in
  the demo.
- The orchestration tool partly overlaps what the agent could do itself (search, then save N times).
  Arguments for keeping it:
  - fewer LLM round trips (1 tool call instead of 1 + N), which matters with ~15 requests/min
  - deterministic note format and tags
  - partial-failure handling done in code, not left to the model
- Where three felt tight:
  - no way to delete or edit a note through the agent (a typo in a saved note is permanent unless the
    file is edited by hand)
  - no way for the *agent* to read saved notes. The resource exists, but the Agents SDK doesn't expose
    MCP resources to the model as callable tools.
- With the lite model, tool routing was sensitive. In one run "find 3 recent papers" went to
  `build_reading_list`. Clear, non-overlapping descriptions matter more than tool count.

## Problems hit during the build

- **arXiv query semantics.** `all:Model Context Protocol security` is read as OR across words
  (arXiv's own echo: `all:Model OR all:Context OR ...`, 1.39M hits). Results were Bitcoin/DNS papers.
  Fix: AND every term, which gave 506 hits, all on topic. Found only by running live, because mocked
  tests can't catch "valid but useless" results.
- **arXiv rate limiting.** The first live call timed out at 10 s and follow-up calls got HTTP 429.
  This confirmed the timeout/unavailable error path works, and led to a dedicated
  "rate limited by arXiv" reason.
- **Gemini free tier.**
  - `gemini-flash-latest` is capped at **20 requests/day** on the free tier and was returning 503
    "high demand"
  - failed (503) requests seemed to count toward the daily cap, so retries burned the quota
  - switched the default to `gemini-flash-lite-latest`
  - changed the retry policy: never retry per-day 429s, retry 503 only once
- **Empty replies and wrong tool picks on flash-lite.** Instrumenting `litellm.acompletion` showed
  `finish_reason="stop"` with no content and no tool call, usually once tool calls were in the
  history.
  - Thought signatures were ruled out: the SDK already replays them.
  - Fix: `reasoning_effort="low"` (lite defaults to minimal thinking), plus one nudged retry as a
    safety net.
- **Agent filling in missing details.** `ReadingListResult` has no authors or dates, so the model
  wrote "Author: (see arXiv)" and "Published: ... (approx)". Fixed with an instruction to only state
  facts the tools returned.
- **"Save abc123" demo.** The model might reject the ID itself instead of calling the tool, which
  would hide the server-side validation. The instructions tell it to pass user-typed IDs as-is and let
  the tool validate.
- **Duplicate demo isn't repeatable on a real notes file** (query 2 fails as a duplicate on the 2nd
  run). The demo script uses a fresh temp notes file by default.
- **stdout is the MCP channel.** Server logging must go to stderr only. Used a dedicated logger with
  a stderr handler and `show_banner=False`.
- **Schema constraints vs. readable errors.** Putting `ge`/`le` in the JSON schema would give raw
  Pydantic validation errors. Validating in code gives a `ToolError` that names the range.
- **Library churn.**
  - FastMCP is on 4.x (decorators return plain functions; `inputSchema` is now `input_schema`)
  - the Agents SDK labels LiteLLM "beta, best-effort" and pins litellm (1.83 installed, 1.106 latest)
  - checking the docs and introspecting the installed packages beat relying on memory
- **Windows console encoding.** A `…` in truncated abstracts printed as garbage, so it was switched
  to `...`. `PYTHONUTF8=1` is set for the server subprocess.

## Possible next steps

- `delete_note` / `update_note` (Action): the most obvious gap.
- `summarize_paper` (Data): fetch the full abstract or PDF and summarize it. Keep the fetch separate
  from saving.
- A PDF-fetch tool (`get_paper_pdf`) feeding the summarizer, with a size limit and a timeout.
- SQLite instead of JSON: concurrent access, queries by tag or date, no full-file rewrites.
- Expose saved notes to the agent (a `list_notes` tool, or a host that maps resources to context) so
  it can answer "what have I saved about MCP?".
- Respect arXiv etiquette more strictly: a client-side 3 s rate limiter and caching repeated queries.
- Evaluate tool routing: a small set of prompts with the expected tool, run across models
  (lite vs flash) to measure misroutes.
