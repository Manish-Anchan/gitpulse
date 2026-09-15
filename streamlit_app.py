import json
import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="GitPulse — Codebase Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-5.3")
ALLOWED_ROOT = os.getenv("ALLOWED_ROOT", "/home/manish/gitpulse")

# Custom CSS for clean styling
st.markdown("""
<style>
    .reportview-container { margin-top: -2em; }
    .stChatInput { margin-bottom: 1em; }
    .tool-box {
        background-color: #1e1e2e;
        border-radius: 8px;
        padding: 10px;
        margin-bottom: 8px;
        font-family: monospace;
        font-size: 0.85em;
    }
</style>
""", unsafe_allow_html=True)


# --- SIDEBAR: STATUS & MCP TOOLS ---
with st.sidebar:
    st.title("⚡ GitPulse")
    st.caption("MCP-Powered Codebase Intelligence Agent")

    st.divider()

    # Health Check
    backend_online = False
    tools_list = []
    try:
        health_res = requests.get(f"{API_BASE_URL}/health", timeout=2)
        if health_res.status_code == 200:
            backend_online = True
            st.success("🟢 API Backend: Online")
            tools_res = requests.get(f"{API_BASE_URL}/api/tools", timeout=2)
            if tools_res.status_code == 200:
                tools_list = tools_res.json().get("tools", [])
        else:
            st.error("🔴 API Backend: Unhealthy")
    except Exception:
        st.error("🔴 API Backend: Offline")
        st.info("Run `uv run uvicorn agent.main:app --reload` to start the API.")

    st.markdown(f"**Model:** `{LLM_MODEL}`")
    st.markdown(f"**Target Repo:** `{ALLOWED_ROOT}`")

    st.divider()

    # Tools Explorer
    st.subheader(f"🛠️ Active MCP Tools ({len(tools_list)})")
    if tools_list:
        for tool in tools_list:
            with st.expander(f"`{tool['name']}`"):
                st.write(tool.get("description", "No description"))
    else:
        st.write("Start the FastAPI backend to inspect connected MCP tools.")

    st.divider()
    if st.button("🧹 Clear Chat History", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


# --- MAIN CHAT INTERFACE ---
st.header("⚡ GitPulse Codebase Intelligence")
st.markdown("Ask questions, inspect commits, search code, and analyze technical debt across your repository.")

# Initialize session state for chat history
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "👋 Hi! I'm **GitPulse**. I have direct access to your repository via MCP tools. "
                "I can inspect git history, diffs, search files, read code securely, and detect TODOs. "
                "What would you like to explore?"
            )
        }
    ]

# Render existing chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Quick Prompt Suggestions (if only the greeting is present)
if len(st.session_state.messages) == 1:
    st.markdown("**Quick Prompts:**")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📜 What are the recent git commits?", use_container_width=True):
            st.session_state.next_prompt = "What are the recent git commits in this repository?"
            st.rerun()
        if st.button("🔍 Find all TODO or FIXME comments", use_container_width=True):
            st.session_state.next_prompt = "Are there any TODO, FIXME, or HACK comments in the project?"
            st.rerun()
    with col2:
        if st.button("📁 List all files in the project root", use_container_width=True):
            st.session_state.next_prompt = "List all files and directories in the project root."
            st.rerun()
        if st.button("🛡️ Inspect mcp_server/server.py security guards", use_container_width=True):
            st.session_state.next_prompt = "Read mcp_server/server.py and explain how path traversal security is implemented."
            st.rerun()

# Handle next prompt from button or chat input
prompt = st.chat_input("Ask about your codebase...")
if hasattr(st.session_state, "next_prompt") and st.session_state.next_prompt:
    prompt = st.session_state.next_prompt
    st.session_state.next_prompt = None

if prompt:
    # 1. Display user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Call backend streaming endpoint
    with st.chat_message("assistant"):
        if not backend_online:
            error_msg = "❌ Cannot connect to FastAPI backend at `http://localhost:8000`. Please start it with `uv run uvicorn agent.main:app --reload`."
            st.error(error_msg)
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
        else:
            status_container = st.status("🧠 Agent is reasoning...", expanded=True)
            response_placeholder = st.empty()
            accumulated_answer = ""

            try:
                # Stream Server-Sent Events (SSE)
                with requests.post(
                    f"{API_BASE_URL}/api/chat/stream",
                    json={"query": prompt, "max_steps": 8},
                    stream=True,
                    timeout=120,
                ) as res:
                    if res.status_code != 200:
                        status_container.update(label="❌ Agent Execution Error", state="error")
                        st.error(f"Error {res.status_code}: {res.text}")
                    else:
                        for line in res.iter_lines():
                            if not line:
                                continue
                            decoded_line = line.decode("utf-8")
                            if not decoded_line.startswith("data: "):
                                continue

                            payload_raw = decoded_line[6:]
                            if payload_raw == "[DONE]":
                                break

                            try:
                                event = json.loads(payload_raw)
                            except Exception:
                                continue

                            event_type = event.get("type")

                            if event_type == "status":
                                status_container.write(f"🔄 {event.get('content')}")

                            elif event_type == "tool_call":
                                tool_name = event.get("tool")
                                args = event.get("args")
                                status_container.write(f"⚙️ **Calling Tool:** `{tool_name}` with args `{json.dumps(args)}`")

                            elif event_type == "tool_result":
                                tool_name = event.get("tool")
                                preview = event.get("preview")
                                with status_container:
                                    st.code(preview, language="text")

                            elif event_type == "final_answer":
                                accumulated_answer = event.get("content", "")
                                response_placeholder.markdown(accumulated_answer)

                            elif event_type == "error":
                                status_container.update(label="⚠️ Error Encountered", state="error")
                                st.error(event.get("content"))

                        status_container.update(label="✅ Reasoning & Verification Complete", state="complete", expanded=False)

                        if accumulated_answer:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": accumulated_answer
                            })

            except requests.exceptions.RequestException as e:
                status_container.update(label="❌ Network Failure", state="error")
                st.error(f"Connection failed: {str(e)}")
