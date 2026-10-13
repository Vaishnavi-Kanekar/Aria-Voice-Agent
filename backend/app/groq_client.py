"""Thin Groq (OpenAI-compatible) client shared by the agent and the summariser."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Groq HTTP {status}: {body[:300]}")
        self.status, self.body = status, body


def make_client(api_key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=GROQ_BASE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(30.0, connect=10.0),
    )


async def complete(client: httpx.AsyncClient, payload: dict) -> dict:
    """Non-streaming chat completion; returns the first choice."""
    r = await client.post("/chat/completions", json=payload)
    if r.status_code != 200:
        raise GroqError(r.status_code, r.text)
    return r.json()["choices"][0]


async def stream_chunks(client: httpx.AsyncClient, payload: dict) -> AsyncIterator[dict[str, Any]]:
    """Streaming chat completion; yields each parsed SSE chunk."""
    async with client.stream("POST", "/chat/completions", json={**payload, "stream": True}) as r:
        if r.status_code != 200:
            raise GroqError(r.status_code, (await r.aread()).decode("utf-8", "replace"))
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
