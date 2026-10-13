"""One phone-style call: browser mic -> STT -> LLM -> TTS -> browser speaker.

WebSocket protocol
  client -> server   binary: PCM16 mono 16 kHz mic audio
                     text:   {"type":"start"} | {"type":"end"}
  server -> client   binary: PCM16 mono audio at `tts_sample_rate`
                     text:   ready | state | partial | user_text | agent_text | metrics |
                             clear_audio | error | summary
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .config import Settings
from .orders import OrderStore
from .speech import speakable
from .summary import fallback_outcome
from .tools import ToolContext

log = logging.getLogger("aura.session")

GREETING = "Hi, this is Aria from Aura Skincare. How can I help you today?"
REPROMPT = "Sorry, I didn't quite catch that. Could you please say it again?"
IDLE_PROMPT = "Are you still there? I'm happy to help whenever you're ready."
TECH_FALLBACK = "Sorry, I'm having a little trouble right now. Could you please say that again?"
INTERRUPT_NOTE = "[The customer interrupted you while you were speaking.] "

Producer = Callable[[Callable[[str], Awaitable[None]]], Awaitable[None]]


@dataclass
class TurnState:
    tid: int
    t_start: float
    spoken: list[str] = field(default_factory=list)  # sentences that began playing
    out: list[dict] = field(default_factory=list)  # new LLM messages (see LLMAgent.run_turn)
    first_audio_ms: int | None = None
    interrupted: bool = False


class CallSession:
    def __init__(self, ws, settings: Settings, stt, tts, agent, summarizer) -> None:
        self.ws = ws
        self.s = settings
        self.stt, self.tts, self.agent, self.summarizer = stt, tts, agent, summarizer

        self.ctx = ToolContext(store=OrderStore())
        self.history: list[dict] = []
        self.transcript: list[dict] = []

        self._send_lock = asyncio.Lock()
        self._client_gone = False
        self._mic_open = False
        self._state: str | None = None
        self._turn_task: asyncio.Task | None = None
        self._turn_seq = 0
        self._stt_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None

        self._utt_text: list[str] = []
        self._utt_conf: list[float] = []
        self._interrupt_note = False
        self._play_until = 0.0  # monotonic time the client's speaker should run dry
        self._last_user_activity = time.monotonic()
        self._idle_prompted = False
        self._t0 = time.monotonic()

    # ------------------------------------------------------------------ outbound helpers
    async def _send_json(self, payload: dict) -> None:
        if self._client_gone:
            return
        try:
            async with self._send_lock:
                await self.ws.send_json(payload)
        except Exception:
            self._client_gone = True

    async def _send_audio(self, chunk: bytes) -> None:
        if self._client_gone:
            return
        try:
            async with self._send_lock:
                await self.ws.send_bytes(chunk)
        except Exception:
            self._client_gone = True
            return
        # Track when the client will finish playing what we have sent so far.
        self._play_until = max(self._play_until, time.monotonic()) + len(chunk) / (self.s.tts_sample_rate * 2)

    async def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            await self._send_json({"type": "state", "state": state})

    def _add_transcript(self, role: str, text: str, **extra) -> None:
        self.transcript.append({"role": role, "text": text, "t": round(time.monotonic() - self._t0, 1), **extra})

    def _busy(self) -> bool:
        running = self._turn_task is not None and not self._turn_task.done()
        return running or time.monotonic() < self._play_until

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        try:
            await self._wait_for_start()
            await self.stt.connect()
        except Exception as e:
            log.exception("could not start call")
            await self._send_json({"type": "error", "fatal": True, "message": f"Could not start the call: {e}"})
            return

        await self._send_json({"type": "ready", "tts_sample_rate": self.s.tts_sample_rate})
        self._mic_open = True
        self._t0 = time.monotonic()
        self._stt_task = asyncio.create_task(self._stt_loop())
        self._idle_task = asyncio.create_task(self._idle_loop())
        self._launch_canned(GREETING)

        send_summary = False
        try:
            send_summary = await self._receive_loop()
        finally:
            await self._shutdown()
        if send_summary and not self._client_gone:
            await self._send_summary()

    async def _wait_for_start(self) -> None:
        msg = await asyncio.wait_for(self.ws.receive(), timeout=15)
        if msg["type"] == "websocket.disconnect":
            raise RuntimeError("client disconnected")
        data = json.loads(msg.get("text") or "{}")
        if data.get("type") != "start":
            raise RuntimeError("expected a start message")

    async def _receive_loop(self) -> bool:
        """Returns True if the client asked to end the call (so a summary should be sent)."""
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                self._client_gone = True
                return False
            if msg.get("bytes") is not None:
                if self._mic_open:
                    try:
                        await self.stt.send_audio(msg["bytes"])
                    except Exception:
                        pass  # the STT loop reports a dropped connection
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except ValueError:
                    continue
                if data.get("type") == "end":
                    return True

    async def _shutdown(self) -> None:
        self._mic_open = False
        tasks = [t for t in (self._idle_task, self._turn_task, self._stt_task) if t]
        for task in tasks:
            if not task.done():
                task.cancel()
        # Closing the STT socket runs as its own task, so it still completes if this handler is
        # itself cancelled (e.g. server shutdown) and the Deepgram connection never leaks.
        closer = asyncio.ensure_future(self.stt.close())
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.shield(closer)

    async def _send_summary(self) -> None:
        try:
            outcome = await asyncio.wait_for(self.summarizer.summarize(self.transcript, self.ctx), timeout=25)
        except Exception:
            log.exception("summary failed")
            outcome = fallback_outcome(self.transcript, self.ctx)
        await self._send_json(
            {
                "type": "summary",
                "duration_s": round(time.monotonic() - self._t0, 1),
                "transcript": self.transcript,
                "outcome": outcome,
                "tool_calls": self.ctx.tool_log,
            }
        )

    # ------------------------------------------------------------------ speech recognition events
    async def _stt_loop(self) -> None:
        try:
            async for ev in self.stt.events():
                await self._on_stt_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("STT stream failed")
            await self._send_json(
                {"type": "error", "fatal": True, "message": "Lost connection to speech recognition. Please end the call and retry."}
            )

    async def _on_stt_event(self, ev) -> None:
        if ev.kind == "interim":
            if not ev.text:
                return
            self._touch()
            live = " ".join(self._utt_text + [ev.text])
            await self._send_json({"type": "partial", "text": live})
            # Barge-in: real words (not a cough or echo blip) while Aria is talking or thinking.
            if self._busy() and len(live.split()) >= self.s.barge_in_min_words:
                await self._interrupt()
        elif ev.kind == "final":
            if ev.text:
                self._touch()
                self._utt_text.append(ev.text)
                self._utt_conf.append(ev.confidence)
            if ev.speech_final:
                await self._flush_utterance()
        elif ev.kind == "utterance_end":
            await self._flush_utterance()

    def _touch(self) -> None:
        self._last_user_activity = time.monotonic()
        self._idle_prompted = False

    async def _flush_utterance(self) -> None:
        text = " ".join(self._utt_text).strip()
        conf = sum(self._utt_conf) / len(self._utt_conf) if self._utt_conf else 1.0
        self._utt_text, self._utt_conf = [], []
        if text:
            await self._handle_utterance(text, conf)

    async def _handle_utterance(self, text: str, conf: float) -> None:
        t_heard = time.monotonic()
        # A one-word "okay"/"hmm" while Aria is talking is a backchannel, not a turn.
        if self._busy() and len(text.split()) < self.s.barge_in_min_words:
            await self._send_json({"type": "partial", "text": ""})
            return
        await self._interrupt()
        self._add_transcript("user", text, confidence=round(conf, 2))
        await self._send_json({"type": "user_text", "text": text})

        if conf < self.s.min_confidence:  # graceful degradation: mumbling / bad audio
            self._launch_canned(REPROMPT, t_heard)
            return
        note, self._interrupt_note = (INTERRUPT_NOTE if self._interrupt_note else ""), False
        self._launch_llm(note + text, t_heard)

    # ------------------------------------------------------------------ turns
    def _new_state(self, t_start: float | None = None) -> TurnState:
        self._turn_seq += 1
        return TurnState(tid=self._turn_seq, t_start=t_start or time.monotonic())

    def _launch_llm(self, user_text: str, t_start: float) -> None:
        state = self._new_state(t_start)

        async def produce(on_text):
            await self.agent.run_turn(self.history, user_text, self.ctx, on_text, state.out)

        self._turn_task = asyncio.create_task(self._run_turn(state, produce))

    def _launch_canned(self, text: str, t_start: float | None = None) -> None:
        state = self._new_state(t_start)

        async def produce(on_text):
            await on_text(text)

        self._turn_task = asyncio.create_task(self._run_turn(state, produce))

    async def _run_turn(self, state: TurnState, produce: Producer) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        worker = asyncio.create_task(self._tts_worker(queue, state))
        try:
            await self._set_state("thinking")
            await produce(queue.put)  # LLM sentences flow to TTS while the LLM is still generating
            await queue.put(None)
            await worker
        except asyncio.CancelledError:
            state.interrupted = True
            worker.cancel()
            raise
        except Exception:
            log.exception("turn failed")
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            try:
                await self._speak(TECH_FALLBACK, state)
            except Exception:
                pass
        finally:
            self._finalize(state)
        await self._set_state("listening")

    async def _tts_worker(self, queue: asyncio.Queue, state: TurnState) -> None:
        while True:
            sentence = await queue.get()
            if sentence is None:
                return
            try:
                await self._speak(sentence, state)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep draining so the text turn still completes; the client is told once per failure.
                log.exception("TTS failed")
                await self._send_json({"type": "error", "fatal": False, "message": "Voice synthesis failed for part of that reply."})

    async def _speak(self, text: str, state: TurnState) -> None:
        state.spoken.append(text)
        await self._send_json({"type": "agent_text", "turn_id": state.tid, "text": text})
        async for chunk in self.tts.stream(speakable(text)):
            if state.first_audio_ms is None:
                state.first_audio_ms = int((time.monotonic() - state.t_start) * 1000)
                await self._send_json({"type": "metrics", "turn_id": state.tid, "first_audio_ms": state.first_audio_ms})
                await self._set_state("speaking")
            await self._send_audio(chunk)

    def _finalize(self, state: TurnState) -> None:
        if state.out:
            self.history.extend(state.out)
        if state.spoken:
            self._add_transcript(
                "agent",
                " ".join(state.spoken),
                interrupted=state.interrupted,
                latency_ms=state.first_audio_ms,
            )
            if state.interrupted:
                self._interrupt_note = True

    async def _interrupt(self) -> None:
        """Stop Aria: cancel the in-flight turn and tell the browser to drop queued audio."""
        was_busy = self._busy()
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task])
        self._play_until = 0.0
        if was_busy:
            await self._send_json({"type": "clear_audio"})
        await self._set_state("listening")

    # ------------------------------------------------------------------ silence handling
    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            if self._idle_prompted or self._busy():
                continue
            quiet_for = time.monotonic() - max(self._last_user_activity, self._play_until)
            if quiet_for > self.s.idle_prompt_seconds:
                self._idle_prompted = True
                self._launch_canned(IDLE_PROMPT)
