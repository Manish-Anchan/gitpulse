import json
import os
from typing import Any, AsyncGenerator, Dict, List, Optional
from dotenv import load_dotenv
from anthropic import AsyncAnthropic
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
        self.base_url = base_url or os.getenv("AGENTROUTER_BASE_URL", "https://agentrouter.org")
        self.model = model or os.getenv("LLM_MODEL", "glm-5.3")

        # Claude Code fingerprint headers required by AgentRouter WAF
        self.client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers={
                "User-Agent": "claude-cli/1.0.108 (external, cli)",
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            },
        )
        self.mcp = mcp_module.mcp

    async def get_anthropic_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Discovers tools from FastMCP and converts them to the Anthropic tool specification.
        """
        mcp_tools = await self.mcp.list_tools()
        tools_spec = []
        for tool in mcp_tools:
            tools_spec.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.parameters,
            })
        return tools_spec

    async def execute_mcp_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        Executes a tool on the FastMCP server and extracts the text output.
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
        Executes the ReAct agent loop using the Anthropic Messages protocol.
        """
        tools = await self.get_anthropic_tool_definitions()

        system_instruction = (
            "You are GitPulse, an autonomous Codebase Intelligence Agent. "
            "You have direct access to inspection tools (git_log, git_diff, read_file, search_code, list_directory, find_todos). "
            "Always use your tools to inspect the repository before answering. "
            "Be concise, technical, and accurate."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": user_query}
        ]

        steps_log: List[AgentStep] = []

        for step_idx in range(1, max_steps + 1):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_instruction,
                messages=messages,
                tools=tools if tools else None,
            )

            # Extract any tool calls or text
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]
            thinking_blocks = [b for b in response.content if b.type == "thinking"]

            thought_text = thinking_blocks[0].thinking if thinking_blocks else None

            # Add assistant message to history
            messages.append({"role": "assistant", "content": response.content})

            # If no tools were called, the agent has finished
            if not tool_use_blocks:
                final_answer = "\n".join(b.text for b in text_blocks) if text_blocks else "Task completed."
                return AgentResponse(
                    query=user_query,
                    final_answer=final_answer,
                    steps=steps_log,
                    total_steps=len(steps_log),
                )

            # Execute all requested tools and collect results
            tool_results = []
            for t_block in tool_use_blocks:
                tool_output = await self.execute_mcp_tool(t_block.name, t_block.input)

                steps_log.append(
                    AgentStep(
                        step_number=step_idx,
                        thought=thought_text,
                        tool_name=t_block.name,
                        tool_args=t_block.input,
                        tool_result=tool_output[:500] + ("..." if len(tool_output) > 500 else ""),
                    )
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t_block.id,
                    "content": tool_output,
                })

            # Feed tool observations back to model
            messages.append({"role": "user", "content": tool_results})

        return AgentResponse(
            query=user_query,
            final_answer="Agent reached maximum execution steps without completing the task.",
            steps=steps_log,
            total_steps=len(steps_log),
        )

    async def run_stream(self, user_query: str, max_steps: int = 8) -> AsyncGenerator[str, None]:
        """
        Runs the ReAct loop and streams real-time events via Server-Sent Events (SSE).
        """
        tools = await self.get_anthropic_tool_definitions()

        system_instruction = (
            "You are GitPulse, an autonomous Codebase Intelligence Agent. "
            "You have direct access to inspection tools (git_log, git_diff, read_file, search_code, list_directory, find_todos). "
            "Always use your tools to inspect the repository before answering."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": user_query}
        ]

        yield f"data: {json.dumps({'type': 'status', 'content': 'Analyzing query & discovering tools...'})}\n\n"

        for step_idx in range(1, max_steps + 1):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_instruction,
                messages=messages,
                tools=tools if tools else None,
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]
            thinking_blocks = [b for b in response.content if b.type == "thinking"]

            if thinking_blocks:
                yield f"data: {json.dumps({'type': 'status', 'content': thinking_blocks[0].thinking[:150] + '...'})}\n\n"

            messages.append({"role": "assistant", "content": response.content})

            if not tool_use_blocks:
                final_text = "\n".join(b.text for b in text_blocks) if text_blocks else "Task completed."
                yield f"data: {json.dumps({'type': 'final_answer', 'content': final_text})}\n\n"
                yield "data: [DONE]\n\n"
                return

            tool_results = []
            for t_block in tool_use_blocks:
                yield f"data: {json.dumps({'type': 'tool_call', 'step': step_idx, 'tool': t_block.name, 'args': t_block.input})}\n\n"

                tool_output = await self.execute_mcp_tool(t_block.name, t_block.input)
                preview = tool_output[:300] + ("..." if len(tool_output) > 300 else "")

                yield f"data: {json.dumps({'type': 'tool_result', 'tool': t_block.name, 'preview': preview})}\n\n"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t_block.id,
                    "content": tool_output,
                })

            messages.append({"role": "user", "content": tool_results})

        yield f"data: {json.dumps({'type': 'error', 'content': 'Exceeded maximum reasoning steps.'})}\n\n"
        yield "data: [DONE]\n\n"
