import json
import os
from typing import Any, AsyncGenerator, Dict, List, Optional
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

import mcp_server.server as mcp_module

load_dotenv()


class AgentStep(BaseModel):
    step_number: int
    thought: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None


class AgentResponse(BaseModel):
    query: str
    final_answer: str
    steps: List[AgentStep] = Field(default_factory=list)
    total_steps: int = 0


class GitPulseAgent:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("AGENTROUTER_API_KEY", "")
        self.base_url = base_url or os.getenv("AGENTROUTER_BASE_URL", "https://co.agentrouter.org/v1")
        self.model = model or os.getenv("LLM_MODEL", "glm-5.3")

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        self.mcp = mcp_module.mcp

    async def get_openai_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Discovers tools directly from the FastMCP server and transforms
        them into standard OpenAI-compatible tool specifications.
        """
        mcp_tools = await self.mcp.list_tools()
        tools_spec = []
        for tool in mcp_tools:
            tools_spec.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters,
                },
            })
        return tools_spec

    async def execute_mcp_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        Executes a tool on the MCP server and formats the result as a text string.
        """
        try:
            result = await self.mcp.call_tool(tool_name, tool_args)
            if hasattr(result, "content") and result.content:
                return "\n".join(c.text for c in result.content if hasattr(c, "text"))
            if hasattr(result, "structured_content") and result.structured_content:
                return str(result.structured_content.get("result", ""))
            return str(result)
        except Exception as e:
            return f"Error executing tool '{tool_name}': {str(e)}"

    async def run(self, user_query: str, max_steps: int = 8) -> AgentResponse:
        """
        Executes the ReAct agent loop until a final answer is produced or max_steps is reached.
        """
        tools = await self.get_openai_tool_definitions()

        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are GitPulse, an autonomous Codebase Intelligence Agent. "
                    "You have direct access to inspection tools (git log, diff, read_file, search_code, list_directory, find_todos). "
                    "Always use tools to verify facts before answering questions about this repository. "
                    "Be concise, technical, and accurate."
                ),
            },
            {"role": "user", "content": user_query},
        ]

        steps_log: List[AgentStep] = []

        for step_idx in range(1, max_steps + 1):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
            )
            choice = response.choices[0]
            msg = choice.message

            # Add assistant message to history
            messages.append(msg)

            # If model produced no tool calls, it has finished reasoning
            if not msg.tool_calls:
                return AgentResponse(
                    query=user_query,
                    final_answer=msg.content or "No response generated.",
                    steps=steps_log,
                    total_steps=len(steps_log),
                )

            # Process all tool calls requested by the model in this turn
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except Exception:
                    fn_args = {}

                # Execute tool via MCP
                tool_output = await self.execute_mcp_tool(fn_name, fn_args)

                steps_log.append(
                    AgentStep(
                        step_number=step_idx,
                        thought=msg.content,
                        tool_name=fn_name,
                        tool_args=fn_args,
                        tool_result=tool_output[:500] + ("..." if len(tool_output) > 500 else ""),
                    )
                )

                # Feed observation back to LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_output,
                })

        return AgentResponse(
            query=user_query,
            final_answer="Agent reached maximum execution steps without completing the task.",
            steps=steps_log,
            total_steps=len(steps_log),
        )

    async def run_stream(self, user_query: str, max_steps: int = 8) -> AsyncGenerator[str, None]:
        """
        Runs the agent loop and yields Server-Sent Events (SSE) in real-time as steps occur.
        """
        tools = await self.get_openai_tool_definitions()

        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are GitPulse, an autonomous Codebase Intelligence Agent. "
                    "You have direct access to inspection tools (git log, diff, read_file, search_code, list_directory, find_todos). "
                    "Always use tools to verify facts before answering questions about this repository."
                ),
            },
            {"role": "user", "content": user_query},
        ]

        yield f"data: {json.dumps({'type': 'status', 'content': 'Analyzing query & discovering tools...'})}\n\n"

        for step_idx in range(1, max_steps + 1):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
            )
            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                # Final response reached
                yield f"data: {json.dumps({'type': 'final_answer', 'content': msg.content})}\n\n"
                yield "data: [DONE]\n\n"
                return

            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except Exception:
                    fn_args = {}

                # Emit step notification to client
                yield f"data: {json.dumps({'type': 'tool_call', 'step': step_idx, 'tool': fn_name, 'args': fn_args})}\n\n"

                tool_output = await self.execute_mcp_tool(fn_name, fn_args)

                # Emit truncated tool result to client
                preview = tool_output[:300] + ("..." if len(tool_output) > 300 else "")
                yield f"data: {json.dumps({'type': 'tool_result', 'tool': fn_name, 'preview': preview})}\n\n"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_output,
                })

        yield f"data: {json.dumps({'type': 'error', 'content': 'Exceeded maximum reasoning steps.'})}\n\n"
        yield "data: [DONE]\n\n"
