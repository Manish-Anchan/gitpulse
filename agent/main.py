from functools import lru_cache
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.agent_engine import GitPulseAgent, AgentResponse

app = FastAPI(
    title="GitPulse Codebase Intelligence API",
    description="Autonomous MCP-powered agent for codebase inspection and developer workflows",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache()
def get_agent() -> GitPulseAgent:
    return GitPulseAgent()


class ChatQueryRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="The natural language question or instruction for the agent.",
        examples=["What are the most recent commits and are there any TODO comments in the repo?"]
    )
    max_steps: int = Field(
        default=8,
        ge=1,
        le=15,
        description="Maximum reasoning/tool iterations allowed."
    )


@app.get("/health")
def health_check():
    """Health check endpoint for container monitoring."""
    return {
        "status": "healthy",
        "service": "GitPulse Agent API",
        "version": "1.0.0",
    }


@app.get("/api/tools")
async def list_available_tools(agent: GitPulseAgent = Depends(get_agent)):
    """Inspect all active MCP tools exposed to the agent."""
    tools = await agent.get_anthropic_tool_definitions()
    return {
        "total_tools": len(tools),
        "tools": tools
    }


@app.post("/api/chat", response_model=AgentResponse)
async def chat_endpoint(
    payload: ChatQueryRequest,
    agent: GitPulseAgent = Depends(get_agent)
):
    """
    Executes the agent loop and returns the complete, structured analysis.
    """
    try:
        return await agent.run(user_query=payload.query, max_steps=payload.max_steps)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution failed: {str(e)}"
        )


@app.post("/api/chat/stream")
async def chat_stream_endpoint(
    payload: ChatQueryRequest,
    agent: GitPulseAgent = Depends(get_agent)
):
    """
    Executes the agent loop and streams live thoughts, tool calls, and observations
    using Server-Sent Events (SSE).
    """
    return StreamingResponse(
        agent.run_stream(user_query=payload.query, max_steps=payload.max_steps),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
