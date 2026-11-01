# GitPulse

GitPulse is an MCP-powered codebase intelligence agent. It combines a FastAPI backend, a FastMCP inspection server, and a Streamlit chat interface so you can ask questions about a repository using natural language.

The agent can inspect files, search code, review Git history and diffs, and find TODO-style comments. Tool calls and results can be displayed live in the Streamlit interface through Server-Sent Events (SSE).

## Architecture

```text
Streamlit UI
     │
     │ HTTP / SSE
     ▼
FastAPI agent API
     │
     │ Anthropic Messages API
     ▼
AgentRouter / configured LLM
     │
     │ FastMCP tool calls
     ▼
Repository inspection tools
```

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- An AgentRouter-compatible API key
- A local Git repository to inspect

## Installation

```bash
git clone https://github.com/Manish-Anchan/gitpulse.git
cd gitpulse
uv sync
```

Create a `.env` file in the project root:

```dotenv
AGENTROUTER_API_KEY=your_api_key_here
AGENTROUTER_BASE_URL=https://agentrouter.org
LLM_MODEL=glm-5.3
ALLOWED_ROOT=/absolute/path/to/repository
API_BASE_URL=http://localhost:8000
```

`AGENTROUTER_API_KEY` is required for agent requests. `ALLOWED_ROOT` controls which repository the inspection tools can access; use an absolute path.

## Running GitPulse

Start the FastAPI backend:

```bash
uv run uvicorn agent.main:app --reload
```

In a second terminal, start the Streamlit UI:

```bash
uv run streamlit run streamlit_app.py
```

Open the Streamlit URL shown in the terminal, usually `http://localhost:8501`.

The backend is usually available at `http://localhost:8000`. Interactive API documentation is available at `http://localhost:8000/docs`.

## API Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check backend health |
| `GET` | `/api/tools` | List the tools exposed to the agent |
| `POST` | `/api/chat` | Run an agent request and return a complete response |
| `POST` | `/api/chat/stream` | Run an agent request and stream status, tool, and answer events over SSE |

Example request:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"What are the most recent commits?","max_steps":8}'
```

The `query` must contain between 3 and 2,000 characters. `max_steps` accepts values from 1 through 15.

## Available Inspection Tools

The FastMCP server exposes:

- `read_file` — read a text file inside the allowed repository
- `list_directory` — list files and directories
- `git_log` — inspect recent commits
- `git_diff` — inspect working-tree, staged, or commit diffs
- `find_todos` — find `TODO`, `FIXME`, `HACK`, and `BUG` comments
- `search_code` — search repository contents with Git grep

## Safety Boundaries

Inspection paths are resolved relative to `ALLOWED_ROOT`. The server rejects paths outside that directory and blocks access to `.env` files and `.git` content. Text files larger than 100 KB and binary files are not returned by `read_file`.

Only point `ALLOWED_ROOT` at a repository you are authorized to inspect. Keep `.env` out of version control; it is ignored by the included `.gitignore`.

## Project Layout

```text
agent/
  agent_engine.py  ReAct loop and Anthropic-compatible tool integration
  main.py          FastAPI application and HTTP endpoints
mcp_server/
  server.py        FastMCP tools and repository safety checks
streamlit_app.py   Streamlit chat UI and SSE client
pyproject.toml      Project metadata and dependencies
uv.lock             Locked Python dependency versions
```

## Development

Run the backend without reload when you want a stable local process:

```bash
uv run uvicorn agent.main:app
```

The agent defaults to a maximum of eight reasoning/tool steps in the UI and API examples. Increase or decrease `max_steps` for a specific request within the API's allowed range.