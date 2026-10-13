"""Streaming text-to-speech over ElevenLabs' HTTP streaming endpoint (raw PCM16 out)."""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from .config import Settings

log = logging.getLogger("aura.tts")


class TTSError(RuntimeError):
    pass


class ElevenLabsTTS:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        # One shared client keeps the TLS connection warm across sentences and calls.
        self._client = httpx.AsyncClient(
            base_url="https://api.elevenlabs.io",
            headers={"xi-api-key": settings.elevenlabs_api_key},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16 mono audio chunks (sample rate = settings.tts_sample_rate)."""
        if not text.strip():
            return
        path = f"/v1/text-to-speech/{self.s.elevenlabs_voice_id}/stream"
        params = {"output_format": f"pcm_{self.s.tts_sample_rate}"}
        body = {
            "text": text,
            "model_id": self.s.elevenlabs_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        leftover = b""
        async with self._client.stream("POST", path, params=params, json=body) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise TTSError(f"ElevenLabs returned {resp.status_code}: {detail}")
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                data = leftover + chunk
                cut = len(data) - (len(data) % 2)  # keep samples whole (16-bit)
                leftover = data[cut:]
                if cut:
                    yield data[:cut]

    async def aclose(self) -> None:
        await self._client.aclose()
