"""Streaming speech-to-text over Deepgram's live WebSocket API."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import connect

from .config import Settings

log = logging.getLogger("aura.stt")


@dataclass
class STTEvent:
    kind: str  # "interim" | "final" | "utterance_end"
    text: str = ""
    confidence: float = 1.0
    speech_final: bool = False  # Deepgram's endpointing decided the speaker stopped


class DeepgramSTT:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._ws = None
        self._keepalive: asyncio.Task | None = None

    async def connect(self) -> None:
        params = {
            "model": self.s.deepgram_model,
            "language": self.s.deepgram_language,
            "encoding": "linear16",
            "sample_rate": 16000,
            "channels": 1,
            "interim_results": "true",
            "smart_format": "true",
            "punctuate": "true",
            "endpointing": self.s.stt_endpointing_ms,
            "utterance_end_ms": self.s.stt_utterance_end_ms,
            "vad_events": "true",
        }
        url = f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"
        self._ws = await connect(
            url,
            additional_headers={"Authorization": f"Token {self.s.deepgram_api_key}"},
            open_timeout=10,
            max_size=None,
        )
        self._keepalive = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(4)
                await self._ws.send(json.dumps({"type": "KeepAlive"}))
        except Exception:
            pass

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(pcm16)

    async def events(self) -> AsyncIterator[STTEvent]:
        async for raw in self._ws:
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "Results":
                alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
                text = (alt.get("transcript") or "").strip()
                conf = float(alt.get("confidence", 1.0))
                if msg.get("is_final"):
                    yield STTEvent("final", text, conf, bool(msg.get("speech_final")))
                else:
                    yield STTEvent("interim", text, conf)
            elif kind == "UtteranceEnd":
                yield STTEvent("utterance_end")
            elif kind == "Error":
                log.error("Deepgram error: %s", msg)

    async def close(self) -> None:
        if self._keepalive:
            self._keepalive.cancel()
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
