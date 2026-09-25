"""FastAPI entry point."""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import RedisError
from openai import APIError
from langgraph.errors import GraphRecursionError

from app.agent import InsightAgent
from app.config import Settings, get_settings
from app.db import Database
from app.memory import InMemoryChatHistory, RedisChatHistory
from app.tools import build_tools


def failure(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, (SQLAlchemyError, RedisError)):
        return 503, "A data service is unavailable. Please retry shortly."
    if isinstance(exc, GraphRecursionError):
        return 422, "Analysis reached its step limit. Try a more specific question."
    if isinstance(exc, APIError):
        return 502, "The model provider could not complete the request. Please retry."
    logging.getLogger(__name__).error("Request failed", exc_info=exc)
    return 500, "The request could not be completed. Please retry."


STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Question must not be blank")
        return value.strip()


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sql: list[str]


def build_components(settings: Settings):
    """Wire up the real dependencies (Postgres, Redis, OpenAI)."""
    from langchain_openai import ChatOpenAI

    if not settings.openai_api_key or settings.openai_api_key == "replace-me":
        raise ValueError("Set OPENAI_API_KEY in .env before starting InsightBot")

    db = Database(settings.database_url, settings.max_rows, settings.statement_timeout_ms)
    if settings.redis_url.startswith("memory://"):
        history = InMemoryChatHistory(settings.history_max_messages)
    else:
        import redis.asyncio as redis
        client = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
        history = RedisChatHistory(client, settings.history_max_messages, settings.history_ttl_seconds)
    model = ChatOpenAI(model=settings.openai_model, api_key=settings.openai_api_key or None,
                       temperature=0, streaming=True, timeout=30, max_retries=1)
    agent = InsightAgent(model, build_tools(db), history, settings.agent_max_steps)
    return db, history, agent


def create_app(components=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db, history, agent = components or build_components(get_settings())
        app.state.db, app.state.history, app.state.agent = db, history, agent
        try:
            yield
        finally:
            try:
                await history.close()
            finally:
                db.engine.dispose()

    app = FastAPI(title="InsightBot", version="0.1.0", lifespan=lifespan)

    async def service_error(request, exc):
        status, message = failure(exc)
        return JSONResponse(status_code=status, content={"detail": message})

    for error_type in (SQLAlchemyError, RedisError, APIError, GraphRecursionError):
        app.add_exception_handler(error_type, service_error)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request):
        try:
            await asyncio.wait_for(asyncio.to_thread(request.app.state.db.run_select, "SELECT 1"), 6)
            await asyncio.wait_for(request.app.state.history.ping(), 6)
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    @app.get("/schema")
    def schema(request: Request):
        db: Database = request.app.state.db
        return {t: db.columns(t) for t in db.table_names()}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, request: Request):
        session_id = req.session_id or uuid.uuid4().hex
        try:
            result = await request.app.state.agent.ask(session_id, req.question)
        except Exception as exc:
            status, message = failure(exc)
            raise HTTPException(status, message) from exc
        return ChatResponse(session_id=session_id, **result)

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest, request: Request):
        """Server-Sent Events: one `data: {json}` line per event."""
        session_id = req.session_id or uuid.uuid4().hex
        agent: InsightAgent = request.app.state.agent

        async def events():
            yield _sse({"type": "session", "session_id": session_id})
            try:
                async for event in agent.stream(session_id, req.question):
                    yield _sse(event)
            except Exception as e:  # surface failures to the client instead of a dropped stream
                _, message = failure(e)
                yield _sse({"type": "error", "message": message})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/sessions/{session_id}/history")
    async def get_history(session_id: str, request: Request):
        messages = await request.app.state.history.get(session_id)
        if not messages:
            raise HTTPException(404, "Session not found")
        return [{"role": m.type, "content": m.content} for m in messages]

    @app.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str, request: Request):
        await request.app.state.history.clear(session_id)

    return app


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


app = create_app()
