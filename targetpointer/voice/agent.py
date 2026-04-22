from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: opencv-python. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc


DEFAULT_FRAME_STORE = Path("runtime/voice/latest_frame.json")
DEFAULT_VOICE_LLM_MODEL = "gpt-5.4-mini"
DEFAULT_STT_MODEL = "scribe_v2_realtime"
DEFAULT_STT_LANGUAGE = "en"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "alloy"
DEFAULT_TTS_SPEED = 1.0

VOICE_REQUIRED_ENV_VARS = ("OPENAI_API_KEY", "ELEVEN_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


@dataclass(frozen=True)
class VoiceAssistantConfig:
    stt_model: str = DEFAULT_STT_MODEL
    stt_language: str = DEFAULT_STT_LANGUAGE
    llm_model: str = DEFAULT_VOICE_LLM_MODEL
    temperature: float | None = None
    max_output_tokens: int | None = None
    tts_model: str = DEFAULT_TTS_MODEL
    tts_voice: str = DEFAULT_TTS_VOICE
    tts_speed: float = DEFAULT_TTS_SPEED

    def process_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "TARGETPOINTER_STT_MODEL": self.stt_model,
            "TARGETPOINTER_STT_LANGUAGE": self.stt_language,
            "TARGETPOINTER_VOICE_LLM_MODEL": self.llm_model,
            "TARGETPOINTER_TTS_MODEL": self.tts_model,
            "TARGETPOINTER_TTS_VOICE": self.tts_voice,
            "TARGETPOINTER_TTS_SPEED": f"{self.tts_speed:.3f}",
        }
        if self.temperature is not None:
            env["TARGETPOINTER_VOICE_TEMPERATURE"] = f"{self.temperature:.3f}"
        if self.max_output_tokens is not None:
            env["TARGETPOINTER_VOICE_MAX_OUTPUT_TOKENS"] = str(self.max_output_tokens)
        return env


@dataclass(frozen=True)
class VoiceFrame:
    data_url: str
    timestamp: str
    tracking_state: str
    bbox: tuple[int, int, int, int] | None
    target_angle: int | None
    output_angle: int | None


def missing_voice_env_vars(environ: dict[str, str] | None = None) -> list[str]:
    environ = os.environ if environ is None else environ
    return [name for name in VOICE_REQUIRED_ENV_VARS if not environ.get(name)]


def optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return float(value)


def optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return int(value)


def env_text_default(name: str, fallback: str) -> str:
    value = os.getenv(name)
    return fallback if value is None or value.strip() == "" else value.strip()


def env_float_default(name: str, fallback: float) -> float:
    value = optional_float_env(name)
    return fallback if value is None else value


def env_int_default(name: str) -> int | None:
    return optional_int_env(name)


def voice_config_defaults_from_env() -> VoiceAssistantConfig:
    return VoiceAssistantConfig(
        stt_model=env_text_default("TARGETPOINTER_STT_MODEL", DEFAULT_STT_MODEL),
        stt_language=env_text_default("TARGETPOINTER_STT_LANGUAGE", DEFAULT_STT_LANGUAGE),
        llm_model=env_text_default("TARGETPOINTER_VOICE_LLM_MODEL", DEFAULT_VOICE_LLM_MODEL),
        temperature=optional_float_env("TARGETPOINTER_VOICE_TEMPERATURE"),
        max_output_tokens=optional_int_env("TARGETPOINTER_VOICE_MAX_OUTPUT_TOKENS"),
        tts_model=env_text_default("TARGETPOINTER_TTS_MODEL", DEFAULT_TTS_MODEL),
        tts_voice=env_text_default("TARGETPOINTER_TTS_VOICE", DEFAULT_TTS_VOICE),
        tts_speed=env_float_default("TARGETPOINTER_TTS_SPEED", DEFAULT_TTS_SPEED),
    )


def encode_frame_data_url(frame, *, max_side: int = 768, quality: int = 70) -> str:
    height, width = frame.shape[:2]
    output = frame
    largest_side = max(height, width)
    if largest_side > max_side:
        scale = max_side / largest_side
        output = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)

    ok, encoded = cv2.imencode(".jpg", output, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return "data:image/jpeg;base64," + base64.b64encode(bytes(encoded)).decode("ascii")


def write_latest_voice_frame(
    store_path: Path | str,
    frame,
    *,
    tracking_state: str,
    bbox: tuple[int, int, int, int] | None,
    target_angle: int | None,
    output_angle: int | None,
    timestamp: datetime | None = None,
    max_side: int = 768,
    quality: int = 70,
) -> VoiceFrame:
    timestamp = timestamp or datetime.now(timezone.utc)
    voice_frame = VoiceFrame(
        data_url=encode_frame_data_url(frame, max_side=max_side, quality=quality),
        timestamp=timestamp.isoformat(timespec="seconds"),
        tracking_state=tracking_state,
        bbox=bbox,
        target_angle=target_angle,
        output_angle=output_angle,
    )
    payload = {
        "data_url": voice_frame.data_url,
        "timestamp": voice_frame.timestamp,
        "tracking_state": voice_frame.tracking_state,
        "bbox": list(voice_frame.bbox) if voice_frame.bbox is not None else None,
        "target_angle": voice_frame.target_angle,
        "output_angle": voice_frame.output_angle,
    }

    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=store_path.parent, delete=False) as tmp:
        json.dump(payload, tmp)
        tmp_path = Path(tmp.name)
    tmp_path.replace(store_path)
    return voice_frame


def load_latest_voice_frame(store_path: Path | str = DEFAULT_FRAME_STORE) -> VoiceFrame | None:
    store_path = Path(store_path)
    if not store_path.exists():
        return None
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    bbox_payload = payload.get("bbox")
    bbox = tuple(int(value) for value in bbox_payload) if bbox_payload is not None else None
    return VoiceFrame(
        data_url=str(payload["data_url"]),
        timestamp=str(payload["timestamp"]),
        tracking_state=str(payload.get("tracking_state", "unknown")),
        bbox=bbox,
        target_angle=_optional_int(payload.get("target_angle")),
        output_angle=_optional_int(payload.get("output_angle")),
    )


def should_sample_frame(last_sample_monotonic: float | None, now_monotonic: float, interval_seconds: float = 5.0) -> bool:
    return last_sample_monotonic is None or now_monotonic - last_sample_monotonic >= interval_seconds


def build_frame_context_text(frame: VoiceFrame) -> str:
    return (
        "Latest TargetPointer camera frame for this user turn. "
        f"Captured at {frame.timestamp}. "
        f"Tracking state: {frame.tracking_state}. "
        f"Selected target bbox: {frame.bbox if frame.bbox is not None else 'none'}. "
        f"Target angle: {frame.target_angle if frame.target_angle is not None else 'unknown'}. "
        f"Servo output angle: {frame.output_angle if frame.output_angle is not None else 'unknown'}."
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def run_livekit_agent(frame_store: Path | str = DEFAULT_FRAME_STORE) -> None:
    from livekit import agents
    from livekit.agents import Agent, AgentSession, ChatContext, ChatMessage
    from livekit.agents.llm import ImageContent
    from livekit.plugins import elevenlabs
    from livekit.plugins import openai

    class TargetPointerVoiceAgent(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=(
                    "You are the TargetPointer voice assistant. Answer questions about the current fixed-camera demo. "
                    "Use the latest injected image when present. Describe visible scene facts and tracking state. "
                    "Do not identify people or infer sensitive traits."
                )
            )

        async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
            del new_message
            frame = load_latest_voice_frame(frame_store)
            if frame is None:
                return
            turn_ctx.add_message(
                role="user",
                content=[
                    build_frame_context_text(frame),
                    ImageContent(image=frame.data_url),
                ],
            )

    async def entrypoint(ctx: agents.JobContext) -> None:
        await ctx.connect()
        llm_kwargs: dict[str, Any] = {
            "model": os.getenv("TARGETPOINTER_VOICE_LLM_MODEL") or DEFAULT_VOICE_LLM_MODEL,
        }
        temperature = optional_float_env("TARGETPOINTER_VOICE_TEMPERATURE")
        max_output_tokens = optional_int_env("TARGETPOINTER_VOICE_MAX_OUTPUT_TOKENS")
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            llm_kwargs["max_output_tokens"] = max_output_tokens

        session = AgentSession(
            stt=elevenlabs.STT(
                model_id=os.getenv("TARGETPOINTER_STT_MODEL") or DEFAULT_STT_MODEL,
                language_code=os.getenv("TARGETPOINTER_STT_LANGUAGE") or DEFAULT_STT_LANGUAGE,
            ),
            llm=openai.responses.LLM(**llm_kwargs),
            tts=openai.TTS(
                model=os.getenv("TARGETPOINTER_TTS_MODEL") or DEFAULT_TTS_MODEL,
                voice=os.getenv("TARGETPOINTER_TTS_VOICE") or DEFAULT_TTS_VOICE,
                speed=optional_float_env("TARGETPOINTER_TTS_SPEED") or DEFAULT_TTS_SPEED,
            ),
        )
        await session.start(room=ctx.room, agent=TargetPointerVoiceAgent())
        await session.generate_reply(instructions="Briefly greet the operator and say you can answer questions about the current view.")

    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))


def main() -> int:
    parser = argparse.ArgumentParser(description="LiveKit voice assistant worker for TargetPointer.")
    parser.add_argument("--frame-store", default=str(DEFAULT_FRAME_STORE), help="Path to latest sampled frame JSON.")
    parser.add_argument("--check-env", action="store_true", help="Only validate required environment variables.")
    args, remaining = parser.parse_known_args()

    from dotenv import load_dotenv

    load_dotenv()
    missing = missing_voice_env_vars()
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 2
    if args.check_env:
        print("Voice assistant environment is ready.")
        return 0

    # Preserve LiveKit CLI subcommands/options after our lightweight wrapper args.
    import sys

    sys.argv = [sys.argv[0], *remaining]
    run_livekit_agent(args.frame_store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
