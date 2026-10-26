# Part 1: MCP Summary

## The Model Context Protocol

LLMs are capable but isolated, because they only know their training data. Before MCP, every link between an AI app and a tool or data source needed custom glue code, so M apps and N tools meant M×N fragile integrations. Anthropic released MCP in November 2024 as an open standard often called "USB-C for AI". A tool is wrapped once as an MCP server, and any MCP-compatible app can use it, which turns M×N into M+N.

MCP defines three roles:

- **Host:** the application the user talks to, such as Claude Desktop, an IDE, or, in my project, the OpenAI Agents SDK script.
- **Client:** lives inside the host and keeps a one-to-one connection with a single server. It handles the protocol itself: JSON-RPC messages over stdio or HTTP.
- **Server:** a lightweight program that exposes capabilities:
  - **tools** — functions the model can call
  - **resources** — read-only data
  - **prompts** — reusable templates

Clients discover a server's tools at runtime, so the model picks tools dynamically instead of a developer hard-coding them. The article also stresses that MCP is not a silver bullet. Trust in third-party servers, authentication for remote servers, and the protocol's young age remain open problems.

## Exploring the Filesystem MCP Server

I explored the reference Filesystem server in the official modelcontextprotocol/servers repository. It exposes about 14 tools, including `read_text_file`, `write_file`, `edit_file`, `list_directory`, `move_file` and `search_files`.

**Strengths.**
- Most tools follow the single-purpose principle: one verb, one job, typed inputs and clear descriptions.
- Behavior is predictable. Every operation is restricted to the allowed directories set at startup, or to roots sent by the client, and paths outside them are rejected.
- Tools carry annotations (read-only, idempotent, destructive), so the host knows which calls are safe.

**Weaknesses.**
- Several tools overlap. `list_directory`, `list_directory_with_sizes` and `directory_tree` do similar jobs, as do `read_text_file` and `read_multiple_files`. These near-duplicates make tool selection harder for the model.
- `edit_file` uses a `dryRun` flag, so one tool does two different things: preview and apply. The README even has to tell the model to always run `dryRun` first.
- `write_file` silently overwrites existing files, which breaks predictable behavior for a destructive action.

Splitting preview from apply and adding an explicit overwrite check would better match the principles of well-defined interfaces and predictable behavior.
