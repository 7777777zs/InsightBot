import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from sqlalchemy import text

from app.db import Database


class ScriptedChatModel(GenericFakeChatModel):
    """Fake LLM that replays a fixed list of AIMessages and accepts bind_tools()."""

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        # The base class cannot stream tool calls, so emulate how OpenAI streams them.
        msg = next(self.messages)
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                for i, tc in enumerate(msg.tool_calls)]))
            return
        for i, word in enumerate(msg.content.split(" ")):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=word if i == 0 else " " + word))
            if run_manager:
                run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'test.db'}", max_rows=50)
    with database.engine.begin() as conn:
        conn.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount NUMERIC, order_date DATE)"))
        conn.execute(text("""INSERT INTO orders (status, amount, order_date) VALUES
            ('completed', 10.5, '2025-01-03'), ('completed', 20, '2025-01-20'),
            ('refunded', 5, '2025-02-01'), ('completed', 100, '2025-02-14')"""))
    yield database
    database.engine.dispose()
