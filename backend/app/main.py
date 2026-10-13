from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .agent import LLMAgent
from .config import Settings
from .orders import public_orders
from .session import CallSession
from .stt import DeepgramSTT
from .summary import Summarizer
from .tts import ElevenLabsTTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aura")

settings = Settings.from_env()
app = FastAPI(title="Aura Skincare Voice Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Heavy clients are created once and shared across calls (warm HTTP connection pools).
_shared: dict = {}


def default_session_factory(ws: WebSocket) -> CallSession:
    if not _shared:
        _shared["agent"] = LLMAgent(settings)
        _shared["tts"] = ElevenLabsTTS(settings)
        _shared["summarizer"] = Summarizer(settings)
    return CallSession(
        ws, settings, DeepgramSTT(settings), _shared["tts"], _shared["agent"], _shared["summarizer"]
    )


app.state.session_factory = default_session_factory


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "missing_keys": settings.missing_keys(),
        "llm_model": settings.groq_model,
        "stt": f"deepgram/{settings.deepgram_model} ({settings.deepgram_language})",
        "tts": f"elevenlabs/{settings.elevenlabs_model}",
    }


@app.get("/api/orders")
def orders() -> list[dict]:
    return public_orders()


@app.websocket("/ws/call")
async def call(ws: WebSocket) -> None:
    await ws.accept()
    # Only the default (real-vendor) factory needs keys; tests inject their own.
    if app.state.session_factory is default_session_factory and settings.missing_keys():
        await ws.send_json(
            {"type": "error", "fatal": True, "message": f"Server is missing API keys: {', '.join(settings.missing_keys())}. See backend/.env.example."}
        )
        await ws.close()
        return
    session = app.state.session_factory(ws)
    try:
        await session.run()
    except Exception:
        log.exception("call crashed")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# Serve the built React app (production / single-link demo). In dev, Vite serves it instead.
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
