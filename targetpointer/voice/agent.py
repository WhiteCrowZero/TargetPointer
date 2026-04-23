from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
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
DEFAULT_CONTROL_STORE = Path("runtime/voice/control.json")
DEFAULT_TRANSCRIPT_STORE = Path("runtime/voice/transcript.jsonl")
DEFAULT_STATUS_STORE = Path("runtime/voice/status.json")
DEFAULT_VOICE_LLM_MODEL = "gpt-4o-mini"
DEFAULT_STT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_STT_LANGUAGE = "zh"
DEFAULT_TTS_MODEL = "eleven_turbo_v2_5"
DEFAULT_TTS_VOICE = "l7kNoIfnJKPg7779LI2t"
DEFAULT_TTS_SPEED = 1.0
DEFAULT_ELEVEN_STABILITY = 0.50
DEFAULT_ELEVEN_SIMILARITY_BOOST = 0.75
DEFAULT_VAD_ACTIVATION_THRESHOLD = 0.75
DEFAULT_VAD_PREFIX_PADDING_MS = 300
DEFAULT_VAD_SILENCE_DURATION_MS = 450

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
    eleven_stability: float = DEFAULT_ELEVEN_STABILITY
    eleven_similarity_boost: float = DEFAULT_ELEVEN_SIMILARITY_BOOST
    vad_activation_threshold: float = DEFAULT_VAD_ACTIVATION_THRESHOLD
    vad_prefix_padding_ms: int = DEFAULT_VAD_PREFIX_PADDING_MS
    vad_silence_duration_ms: int = DEFAULT_VAD_SILENCE_DURATION_MS

    def process_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "TARGETPOINTER_STT_MODEL": self.stt_model,
            "TARGETPOINTER_STT_LANGUAGE": self.stt_language,
            "TARGETPOINTER_VOICE_LLM_MODEL": self.llm_model,
            "TARGETPOINTER_TTS_MODEL": self.tts_model,
            "TARGETPOINTER_TTS_VOICE": self.tts_voice,
            "TARGETPOINTER_TTS_SPEED": f"{self.tts_speed:.3f}",
            "TARGETPOINTER_ELEVEN_STABILITY": f"{self.eleven_stability:.3f}",
            "TARGETPOINTER_ELEVEN_SIMILARITY_BOOST": f"{self.eleven_similarity_boost:.3f}",
            "TARGETPOINTER_VAD_ACTIVATION_THRESHOLD": f"{self.vad_activation_threshold:.3f}",
            "TARGETPOINTER_VAD_PREFIX_PADDING_MS": str(self.vad_prefix_padding_ms),
            "TARGETPOINTER_VAD_SILENCE_DURATION_MS": str(self.vad_silence_duration_ms),
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


@dataclass(frozen=True)
class VoiceTranscriptLine:
    role: str
    text: str
    is_final: bool
    timestamp: str


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


def env_int_with_default(name: str, fallback: int) -> int:
    value = optional_int_env(name)
    return fallback if value is None else value


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
        eleven_stability=env_float_default("TARGETPOINTER_ELEVEN_STABILITY", DEFAULT_ELEVEN_STABILITY),
        eleven_similarity_boost=env_float_default(
            "TARGETPOINTER_ELEVEN_SIMILARITY_BOOST",
            DEFAULT_ELEVEN_SIMILARITY_BOOST,
        ),
        vad_activation_threshold=env_float_default(
            "TARGETPOINTER_VAD_ACTIVATION_THRESHOLD",
            DEFAULT_VAD_ACTIVATION_THRESHOLD,
        ),
        vad_prefix_padding_ms=env_int_with_default(
            "TARGETPOINTER_VAD_PREFIX_PADDING_MS",
            DEFAULT_VAD_PREFIX_PADDING_MS,
        ),
        vad_silence_duration_ms=env_int_with_default(
            "TARGETPOINTER_VAD_SILENCE_DURATION_MS",
            DEFAULT_VAD_SILENCE_DURATION_MS,
        ),
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


def write_voice_control(store_path: Path | str = DEFAULT_CONTROL_STORE, *, user_muted: bool = False) -> None:
    payload = {"user_muted": bool(user_muted), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _write_json_atomic(store_path, payload)


def load_voice_control(store_path: Path | str = DEFAULT_CONTROL_STORE) -> dict[str, Any]:
    store_path = Path(store_path)
    if not store_path.exists():
        return {"user_muted": False}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"user_muted": False}
    return {"user_muted": bool(payload.get("user_muted", False))}


def clear_voice_transcript(store_path: Path | str = DEFAULT_TRANSCRIPT_STORE) -> None:
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("", encoding="utf-8")


def append_voice_transcript(
    store_path: Path | str,
    *,
    role: str,
    text: str,
    is_final: bool = True,
    timestamp: datetime | None = None,
) -> VoiceTranscriptLine:
    timestamp = timestamp or datetime.now(timezone.utc)
    line = VoiceTranscriptLine(
        role=role,
        text=text.strip(),
        is_final=is_final,
        timestamp=timestamp.isoformat(timespec="seconds"),
    )
    if not line.text:
        return line
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with store_path.open("a", encoding="utf-8") as handle:
        json.dump(
            {
                "role": line.role,
                "text": line.text,
                "is_final": line.is_final,
                "timestamp": line.timestamp,
            },
            handle,
            ensure_ascii=False,
        )
        handle.write("\n")
    return line


def load_voice_transcript_lines(store_path: Path | str = DEFAULT_TRANSCRIPT_STORE, *, limit: int = 80) -> list[VoiceTranscriptLine]:
    store_path = Path(store_path)
    if not store_path.exists():
        return []
    lines: list[VoiceTranscriptLine] = []
    for raw_line in store_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        lines.append(
            VoiceTranscriptLine(
                role=str(payload.get("role", "system")),
                text=str(payload.get("text", "")),
                is_final=bool(payload.get("is_final", True)),
                timestamp=str(payload.get("timestamp", "")),
            )
        )
    return lines


def write_voice_status(
    store_path: Path | str = DEFAULT_STATUS_STORE,
    *,
    user_state: str = "listening",
    agent_state: str = "idle",
    user_muted: bool = False,
) -> None:
    _write_json_atomic(
        store_path,
        {
            "user_state": user_state,
            "agent_state": agent_state,
            "user_muted": user_muted,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


def load_voice_status(store_path: Path | str = DEFAULT_STATUS_STORE) -> dict[str, Any]:
    store_path = Path(store_path)
    if not store_path.exists():
        return {"user_state": "idle", "agent_state": "idle", "user_muted": False}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"user_state": "idle", "agent_state": "idle", "user_muted": False}
    return {
        "user_state": str(payload.get("user_state", "idle")),
        "agent_state": str(payload.get("agent_state", "idle")),
        "user_muted": bool(payload.get("user_muted", False)),
    }


def _write_json_atomic(store_path: Path | str, payload: dict[str, Any]) -> None:
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=store_path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(store_path)


def should_sample_frame(last_sample_monotonic: float | None, now_monotonic: float, interval_seconds: float = 5.0) -> bool:
    return last_sample_monotonic is None or now_monotonic - last_sample_monotonic >= interval_seconds


def build_frame_context_text(frame: VoiceFrame) -> str:
    return (
        "这是本轮用户提问时 TargetPointer 的最新摄像头画面。"
        f"采集时间：{frame.timestamp}。"
        f"跟踪状态：{frame.tracking_state}。"
        f"选中目标框：{frame.bbox if frame.bbox is not None else '无'}。"
        f"目标角：{frame.target_angle if frame.target_angle is not None else '未知'}。"
        f"舵机输出角：{frame.output_angle if frame.output_angle is not None else '未知'}。"
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chat_message_text(item: Any) -> str:
    content = getattr(item, "content", None)
    if content is None and isinstance(item, dict):
        content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
            else:
                text = getattr(part, "text", None)
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()
    return ""


async def _poll_voice_control(
    session: Any,
    control_store: Path | str,
    status_store: Path | str,
    state: dict[str, str | bool],
) -> None:
    last_muted: bool | None = None
    while True:
        control = load_voice_control(control_store)
        muted = bool(control.get("user_muted", False))
        if muted != last_muted:
            if muted:
                with contextlib.suppress(Exception):
                    session.interrupt(force=True)
            with contextlib.suppress(Exception):
                session.input.set_audio_enabled(not muted)
            last_muted = muted
            state["user_muted"] = muted
            write_voice_status(
                status_store,
                user_state=str(state.get("user_state", "listening")),
                agent_state=str(state.get("agent_state", "idle")),
                user_muted=muted,
            )
        await asyncio.sleep(0.2)


def run_livekit_agent(
    frame_store: Path | str = DEFAULT_FRAME_STORE,
    *,
    control_store: Path | str = DEFAULT_CONTROL_STORE,
    transcript_store: Path | str = DEFAULT_TRANSCRIPT_STORE,
    status_store: Path | str = DEFAULT_STATUS_STORE,
) -> None:
    from livekit import agents
    from livekit.agents import Agent, AgentSession, ChatContext, ChatMessage
    from livekit.agents.llm import ImageContent
    from livekit.plugins import elevenlabs, openai, silero

    class TargetPointerVoiceAgent(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=(
                    "你是 TargetPointer 的中文实时语音助手。你只使用当前画面和系统状态回答问题。"
                    "描述时尽量具体，包括背景环境、人物可见穿着、姿态、相对位置、动作和设备跟踪状态；"
                    "不确定就说不确定，不要为了显得完整而编造。"
                    "不要识别个人身份，不要推断民族、宗教、健康、财富、职业等敏感属性。"
                    "回答要适合语音播报，短句、中文、不要 Markdown。"
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
        state: dict[str, str | bool] = {"user_state": "listening", "agent_state": "idle", "user_muted": False}
        write_voice_status(status_store, user_state="listening", agent_state="idle", user_muted=False)
        append_voice_transcript(transcript_store, role="system", text="实时语音 worker 已连接 LiveKit 房间。")

        llm_kwargs: dict[str, Any] = {
            "model": os.getenv("TARGETPOINTER_VOICE_LLM_MODEL") or DEFAULT_VOICE_LLM_MODEL,
        }
        temperature = optional_float_env("TARGETPOINTER_VOICE_TEMPERATURE")
        max_output_tokens = optional_int_env("TARGETPOINTER_VOICE_MAX_OUTPUT_TOKENS")
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            llm_kwargs["max_completion_tokens"] = max_output_tokens

        session = AgentSession(
            vad=silero.VAD.load(
                activation_threshold=env_float_default(
                    "TARGETPOINTER_VAD_ACTIVATION_THRESHOLD",
                    DEFAULT_VAD_ACTIVATION_THRESHOLD,
                ),
                prefix_padding_duration=env_int_with_default(
                    "TARGETPOINTER_VAD_PREFIX_PADDING_MS",
                    DEFAULT_VAD_PREFIX_PADDING_MS,
                )
                / 1000.0,
                min_silence_duration=env_int_with_default(
                    "TARGETPOINTER_VAD_SILENCE_DURATION_MS",
                    DEFAULT_VAD_SILENCE_DURATION_MS,
                )
                / 1000.0,
                force_cpu=True,
            ),
            stt=openai.STT(
                model=os.getenv("TARGETPOINTER_STT_MODEL") or DEFAULT_STT_MODEL,
                language=os.getenv("TARGETPOINTER_STT_LANGUAGE") or DEFAULT_STT_LANGUAGE,
                noise_reduction_type="near_field",
                use_realtime=False,
            ),
            llm=openai.LLM(**llm_kwargs),
            tts=elevenlabs.TTS(
                model=os.getenv("TARGETPOINTER_TTS_MODEL") or DEFAULT_TTS_MODEL,
                voice_id=os.getenv("TARGETPOINTER_TTS_VOICE") or DEFAULT_TTS_VOICE,
                voice_settings=elevenlabs.VoiceSettings(
                    stability=env_float_default("TARGETPOINTER_ELEVEN_STABILITY", DEFAULT_ELEVEN_STABILITY),
                    similarity_boost=env_float_default(
                        "TARGETPOINTER_ELEVEN_SIMILARITY_BOOST",
                        DEFAULT_ELEVEN_SIMILARITY_BOOST,
                    ),
                    speed=optional_float_env("TARGETPOINTER_TTS_SPEED") or DEFAULT_TTS_SPEED,
                    use_speaker_boost=True,
                ),
                language=os.getenv("TARGETPOINTER_STT_LANGUAGE") or DEFAULT_STT_LANGUAGE,
                sync_alignment=True,
            ),
            use_tts_aligned_transcript=True,
            min_endpointing_delay=0.2,
            max_endpointing_delay=2.2,
        )

        def update_status(*, user_state: str | None = None, agent_state: str | None = None) -> None:
            if user_state is not None:
                state["user_state"] = user_state
            if agent_state is not None:
                state["agent_state"] = agent_state
            write_voice_status(
                status_store,
                user_state=str(state.get("user_state", "listening")),
                agent_state=str(state.get("agent_state", "idle")),
                user_muted=bool(state.get("user_muted", False)),
            )

        session.on("user_state_changed", lambda ev: update_status(user_state=ev.new_state))
        session.on("agent_state_changed", lambda ev: update_status(agent_state=ev.new_state))
        session.on(
            "user_input_transcribed",
            lambda ev: append_voice_transcript(
                transcript_store,
                role="user",
                text=ev.transcript,
                is_final=ev.is_final,
            ),
        )
        def record_assistant_message(ev: Any) -> None:
            role = str(getattr(ev.item, "role", "assistant"))
            if role != "assistant":
                return
            append_voice_transcript(
                transcript_store,
                role=role,
                text=_chat_message_text(ev.item),
                is_final=True,
            )

        session.on("conversation_item_added", record_assistant_message)

        await session.start(room=ctx.room, agent=TargetPointerVoiceAgent())
        control_task = asyncio.create_task(_poll_voice_control(session, control_store, status_store, state))
        await session.generate_reply(instructions="用中文和操作者打招呼，说明你可以实时回答当前画面、人物穿着和设备跟踪状态。")
        try:
            await asyncio.Event().wait()
        finally:
            control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await control_task

    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))


def main() -> int:
    parser = argparse.ArgumentParser(description="LiveKit voice assistant worker for TargetPointer.")
    parser.add_argument("--frame-store", default=str(DEFAULT_FRAME_STORE), help="Path to latest sampled frame JSON.")
    parser.add_argument("--control-store", default=str(DEFAULT_CONTROL_STORE), help="Path to voice control JSON.")
    parser.add_argument("--transcript-store", default=str(DEFAULT_TRANSCRIPT_STORE), help="Path to transcript JSONL.")
    parser.add_argument("--status-store", default=str(DEFAULT_STATUS_STORE), help="Path to voice status JSON.")
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
    run_livekit_agent(
        args.frame_store,
        control_store=args.control_store,
        transcript_store=args.transcript_store,
        status_store=args.status_store,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
