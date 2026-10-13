from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str
    summary_model: str

    deepgram_api_key: str
    deepgram_model: str
    deepgram_language: str
    stt_endpointing_ms: int
    stt_utterance_end_ms: int

    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model: str
    tts_sample_rate: int

    barge_in_min_words: int
    min_confidence: float
    idle_prompt_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        model = _str("GROQ_MODEL", "llama-3.3-70b-versatile")
        return cls(
            groq_api_key=_str("GROQ_API_KEY", ""),
            groq_model=model,
            summary_model=_str("SUMMARY_MODEL", model),
            deepgram_api_key=_str("DEEPGRAM_API_KEY", ""),
            deepgram_model=_str("DEEPGRAM_MODEL", "nova-3"),
            deepgram_language=_str("DEEPGRAM_LANGUAGE", "en-IN"),
            stt_endpointing_ms=_int("STT_ENDPOINTING_MS", 350),
            stt_utterance_end_ms=_int("STT_UTTERANCE_END_MS", 1000),
            elevenlabs_api_key=_str("ELEVENLABS_API_KEY", ""),
            elevenlabs_voice_id=_str("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            elevenlabs_model=_str("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
            tts_sample_rate=_int("TTS_SAMPLE_RATE", 24000),
            barge_in_min_words=_int("BARGE_IN_MIN_WORDS", 2),
            min_confidence=_float("MIN_CONFIDENCE", 0.45),
            idle_prompt_seconds=_float("IDLE_PROMPT_SECONDS", 25),
        )

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY")
        if not self.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        return missing
