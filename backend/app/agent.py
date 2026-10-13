"""LLM agent (Groq): streams a reply, runs tool calls, and hands finished sentences to the voice layer."""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from .config import Settings
from .groq_client import GroqError, make_client, stream_chunks
from .prompts import SYSTEM_PROMPT
from .speech import SentenceSplitter
from .tools import TOOL_SCHEMAS, ToolContext, execute_tool

log = logging.getLogger("aura.agent")

MAX_TOOL_ROUNDS = 3
EMPTY_REPLY_FALLBACK = "Sorry, I missed that. Could you please say it again?"

OnText = Callable[[str], Awaitable[None]]


def _as_blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)


def merge_consecutive(messages: list[dict]) -> list[dict]:
    """Merge back-to-back messages with the same role (happens after a barge-in), keeping
    tool_result blocks first inside a user message as the API requires."""
    merged: list[dict] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            blocks = _as_blocks(merged[-1]["content"]) + _as_blocks(m["content"])
            if m["role"] == "user":
                blocks.sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)  # stable
            merged[-1] = {"role": m["role"], "content": blocks}
        else:
            merged.append({"role": m["role"], "content": m["content"]})
    return merged


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """The session keeps history as internal content blocks (text / tool_use / tool_result) (text / tool_use / tool_result).
    Convert it to the OpenAI-style messages Groq expects."""
    out: list[dict] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            text = "".join(b["text"] for b in content if b.get("type") == "text")
            msg: dict = {"role": "assistant", "content": text or None}
            calls = [
                {"id": b["id"], "type": "function",
                 "function": {"name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
                for b in content if b.get("type") == "tool_use"
            ]
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user blocks: tool results first (kept first by merge_consecutive), then any text
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
            text = " ".join(b["text"] for b in content if b.get("type") == "text")
            if text.strip():
                out.append({"role": "user", "content": text})
    return out


def _openai_tools() -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in TOOL_SCHEMAS
    ]


class LLMAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = make_client(settings.groq_api_key)

    def _chunks(self, payload: dict):
        return stream_chunks(self.client, payload)

    async def run_turn(
        self,
        history: list[dict],
        user_text: str,
        ctx: ToolContext,
        on_text: OnText,
        out: list[dict],
    ) -> None:
        """Run one customer turn.

        `out` receives the new messages as they become complete (the user message, then each
        assistant message and its tool results). If the turn is cancelled by a barge-in, `out`
        still holds a consistent prefix that the session can safely add to the history.
        """
        out.append({"role": "user", "content": user_text})
        spoken_chunks = 0

        for _ in range(MAX_TOOL_ROUNDS + 1):
            splitter = SentenceSplitter()
            payload = {
                "model": self.settings.groq_model,
                "max_tokens": 350,
                "temperature": 0.3,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                + to_openai_messages(merge_consecutive(list(history) + out)),
                "tools": _openai_tools(),
            }
            text_parts: list[str] = []
            calls: dict[int, dict] = {}   # streamed tool calls arrive in fragments keyed by index
            finish = None
            for attempt in (1, 2):
                try:
                    async for chunk in self._chunks(payload):
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            for sentence in splitter.feed(delta["content"]):
                                spoken_chunks += 1
                                await on_text(sentence)
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] = fn.get("name") or slot["name"]
                            slot["args"] += fn.get("arguments") or ""
                        finish = choice.get("finish_reason") or finish
                    break
                except GroqError as exc:
                    # Llama occasionally emits a malformed tool call; one retry almost always fixes it.
                    if attempt == 1 and exc.status == 400 and "tool_use_failed" in exc.body and not text_parts:
                        log.warning("Groq tool_use_failed, retrying once")
                        continue
                    raise

            tail = splitter.flush()
            if tail:
                spoken_chunks += 1
                await on_text(tail)

            text = "".join(text_parts)
            blocks: list[dict] = []
            tool_uses = []
            if text.strip():
                blocks.append({"type": "text", "text": text})
            for i, slot in sorted(calls.items()):
                if not slot["name"]:
                    continue
                try:
                    args = json.loads(slot["args"]) if slot["args"].strip() else {}
                except json.JSONDecodeError:
                    args = {}
                tu = {"type": "tool_use", "id": slot["id"] or f"call_{i}", "name": slot["name"], "input": args}
                blocks.append(tu)
                tool_uses.append(tu)

            # No awaits between these two appends, so a cancellation can never leave a
            # tool_use without its tool_result in `out`.
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            if not tool_uses:
                break
            results = []
            for tu in tool_uses:
                result = execute_tool(tu["name"], tu["input"], ctx)
                log.info("tool %s(%s) -> %s", tu["name"], tu["input"], result)
                results.append(
                    {"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(result, ensure_ascii=False)}
                )
            out.append({"role": "user", "content": results})

        if spoken_chunks == 0:
            await on_text(EMPTY_REPLY_FALLBACK)
