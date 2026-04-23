from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import error, request

from targetpointer.voice.voices import DEFAULT_PERSON_VOICE_ID


DEFAULT_REALTIME_CHAT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_REALTIME_CHAT_CLIENT_SOURCE = "targetpointer"
DEFAULT_REALTIME_CHAT_TIMEOUT_S = 10.0
DEFAULT_TARGETPOINTER_USER_IDENTITY = "targetpointer-operator"


@dataclass(frozen=True)
class RealtimeVoiceConfig:
    tts_voice: str = DEFAULT_PERSON_VOICE_ID

    def to_model_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        normalized_tts_voice = str(self.tts_voice or "").strip()
        if normalized_tts_voice:
            settings["tts_voice"] = normalized_tts_voice
        return settings


@dataclass(frozen=True)
class RealtimeVoiceSessionConfig:
    api_base_url: str
    session_id: str
    conversation_id: str
    room: str
    livekit_url: str
    user_identity: str
    user_token: str
    status: str


class RealtimeChatApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def realtime_chat_api_base_url(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = str(env.get("REALTIME_CHAT_API_BASE_URL") or DEFAULT_REALTIME_CHAT_API_BASE_URL).strip()
    return value.rstrip("/")


def format_voice_session_details(session: RealtimeVoiceSessionConfig | None) -> str:
    if session is None:
        return ""
    return (
        f"Backend: {session.api_base_url}\n"
        f"Session: {session.session_id}\n"
        f"Conversation: {session.conversation_id}\n"
        f"Room: {session.room}\n"
        f"User: {session.user_identity}\n"
        f"Status: {session.status}"
    )


def build_realtime_voice_session_payload(
    config: RealtimeVoiceConfig,
    *,
    user_identity: str = DEFAULT_TARGETPOINTER_USER_IDENTITY,
    extra_vars: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    allow_client_ai_mode: bool = False,
) -> dict[str, Any]:
    payload = {
        "agent_id": "multimodal",
        "input_modes": ["audio", "text", "image"],
        "output_modes": ["audio", "text"],
        "user_identity": user_identity,
        "custom_instructions": "始终用中文简洁回答；结合最近上传的时序图片和主要人物进行聊天，图片里会包含锁定人物框。",
        "extra_vars": dict(extra_vars or {}),
        "model_settings": config.to_model_settings(),
        "attachments": list(attachments or []),
    }
    if allow_client_ai_mode:
        payload["ai_mode"] = "pipeline"
    return payload


def parse_realtime_voice_session_config(
    payload: dict[str, Any],
    *,
    api_base_url: str,
) -> RealtimeVoiceSessionConfig:
    return RealtimeVoiceSessionConfig(
        api_base_url=api_base_url,
        session_id=str(payload["session_id"]),
        conversation_id=str(payload["conversation_id"]),
        room=str(payload["room"]),
        livekit_url=str(payload["livekit_url"]),
        user_identity=str(payload.get("user_identity") or DEFAULT_TARGETPOINTER_USER_IDENTITY),
        user_token=str(payload["livekit_token"]),
        status=str(payload.get("status") or "activate"),
    )


class RealtimeChatApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        client_source: str = DEFAULT_REALTIME_CHAT_CLIENT_SOURCE,
        timeout_s: float = DEFAULT_REALTIME_CHAT_TIMEOUT_S,
    ) -> None:
        self.base_url = realtime_chat_api_base_url({"REALTIME_CHAT_API_BASE_URL": base_url or ""})
        self.client_source = client_source
        self.timeout_s = timeout_s

    def health_check(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def get_capabilities(self) -> dict[str, Any]:
        return self._request_json("GET", "/v2/sessions/capabilities")

    def create_session(self, payload: dict[str, Any]) -> RealtimeVoiceSessionConfig:
        response = self._request_json("POST", "/v2/sessions", payload=payload)
        return parse_realtime_voice_session_config(response, api_base_url=self.base_url)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/v2/sessions/{session_id}")

    def reconnect_session(self, session_id: str) -> RealtimeVoiceSessionConfig:
        response = self._request_json("POST", f"/v2/sessions/{session_id}/reconnect")
        return parse_realtime_voice_session_config(response, api_base_url=self.base_url)

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/v2/sessions/{session_id}/close",
            payload={"reason": "client_close"},
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method in {"POST", "PATCH"}:
            headers["Idempotency-Key"] = os.urandom(16).hex()
            headers["X-Client-Source"] = self.client_source

        req = request.Request(url, data=data, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RealtimeChatApiError(
                self._extract_error_message(raw) or f"HTTP {exc.code}",
                status_code=exc.code,
            ) from exc
        except error.URLError as exc:
            raise RealtimeChatApiError(str(exc.reason) or "backend unavailable") from exc
        except TimeoutError as exc:
            raise RealtimeChatApiError("request timed out") from exc

        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RealtimeChatApiError("invalid JSON response from backend") from exc
        if not isinstance(parsed, dict):
            raise RealtimeChatApiError("unexpected response shape from backend")
        return parsed

    @staticmethod
    def _extract_error_message(raw: str) -> str:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
            if isinstance(detail, dict):
                message = detail.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
        return raw.strip()
