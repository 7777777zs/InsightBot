"""Per-session conversation history.

Only the user question and the final answer are stored, not intermediate tool
calls. That keeps history small and avoids orphaned tool messages when old
turns are trimmed.
"""

import json
from typing import Protocol

from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict


class ChatHistory(Protocol):
    async def get(self, session_id: str) -> list[BaseMessage]: ...
    async def append(self, session_id: str, messages: list[BaseMessage]) -> None: ...
    async def clear(self, session_id: str) -> None: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


def _validate_pairs(messages: list[BaseMessage]) -> None:
    if not messages or len(messages) % 2 or any(
        message.type != ("human" if i % 2 == 0 else "ai")
        for i, message in enumerate(messages)
    ):
        raise ValueError("History accepts complete human/assistant pairs only")


class RedisChatHistory:
    def __init__(self, client, max_messages: int = 20, ttl_seconds: int = 86400,
                 prefix: str = "insightbot:history:"):
        self.client = client  # redis.asyncio.Redis
        if ttl_seconds < 1:
            raise ValueError("History TTL must be positive")
        if max_messages < 2 or max_messages % 2:
            raise ValueError("History window must be a positive number of complete pairs")
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self.prefix = prefix

    def _key(self, session_id: str) -> str:
        return self.prefix + session_id

    async def get(self, session_id: str) -> list[BaseMessage]:
        raw = await self.client.lrange(self._key(session_id), -self.max_messages, -1)
        return messages_from_dict([json.loads(r) for r in raw])

    async def append(self, session_id: str, messages: list[BaseMessage]) -> None:
        _validate_pairs(messages)
        key = self._key(session_id)
        pipe = self.client.pipeline()
        for m in messages:
            pipe.rpush(key, json.dumps(message_to_dict(m)))
        pipe.ltrim(key, -self.max_messages, -1)  # sliding window
        pipe.expire(key, self.ttl_seconds)  # idle sessions expire
        await pipe.execute()

    async def close(self) -> None:
        await self.client.aclose()

    async def ping(self) -> bool:
        return await self.client.ping()

    async def clear(self, session_id: str) -> None:
        await self.client.delete(self._key(session_id))


class InMemoryChatHistory:
    """Drop-in replacement for local development and tests."""

    def __init__(self, max_messages: int = 20):
        if max_messages < 2 or max_messages % 2:
            raise ValueError("History window must be a positive number of complete pairs")
        self.max_messages = max_messages
        self._store: dict[str, list[BaseMessage]] = {}

    async def get(self, session_id: str) -> list[BaseMessage]:
        return list(self._store.get(session_id, [])[-self.max_messages:])

    async def append(self, session_id: str, messages: list[BaseMessage]) -> None:
        _validate_pairs(messages)
        msgs = self._store.setdefault(session_id, [])
        msgs.extend(messages)
        del msgs[:-self.max_messages]

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    async def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)

