from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from PySide6 import QtCore

from livekit import rtc
from livekit.rtc._proto import participant_pb2

from targetpointer.ui.realtime_chat import RealtimeVoiceSessionConfig


class DesktopLiveKitClientThread(QtCore.QThread):
    state_changed = QtCore.Signal(str, str)
    system_message = QtCore.Signal(str)
    live_caption_changed = QtCore.Signal(str, str, bool)
    agent_availability_changed = QtCore.Signal(bool)
    failure_reported = QtCore.Signal(str)
    reconnect_requested = QtCore.Signal()

    def __init__(
        self,
        session_config: RealtimeVoiceSessionConfig,
        *,
        start_muted: bool = False,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.session_config = session_config
        self.start_muted = start_muted
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._room: rtc.Room | None = None
        self._devices: rtc.MediaDevices | None = None
        self._input_capture: rtc.InputCapture | None = None
        self._output_player: rtc.OutputPlayer | None = None
        self._local_audio_track: rtc.LocalAudioTrack | None = None
        self._agent_present = False
        self._muted = start_muted
        self._reconnect_emitted = False
        self._caption_segments: dict[str, dict[str, tuple[float, str, bool]]] = {
            "user": {},
            "assistant": {},
        }

    def run(self) -> None:
        asyncio.run(self._run_client())

    def stop_client(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def set_microphone_muted(self, muted: bool) -> None:
        self._muted = muted
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._apply_local_mute)

    async def _run_client(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._room = rtc.Room(loop=self._loop)
        self._bind_room_events(self._room)

        try:
            self.state_changed.emit("connecting", "桌面端正在接入语音会话。")
            await self._room.connect(self.session_config.livekit_url, self.session_config.user_token)

            self._devices = rtc.MediaDevices(loop=self._loop)
            self._input_capture = self._devices.open_input()
            self._output_player = self._devices.open_output()
            await self._output_player.start()

            self._local_audio_track = rtc.LocalAudioTrack.create_audio_track(
                "targetpointer-operator-audio",
                self._input_capture.source,
            )
            await self._room.local_participant.publish_track(
                self._local_audio_track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            )
            self._apply_local_mute()

            self._reconnect_emitted = False
            self.system_message.emit(
                f"已连接语音会话：session={self.session_config.session_id} room={self.session_config.room}"
            )
            self.state_changed.emit("waiting_agent", "已接入会话，等待 AI 音频与字幕流。")
            self._sync_existing_participants()

            await self._stop_event.wait()
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.failure_reported.emit(message)
            self.state_changed.emit("error", f"桌面端 LiveKit client 连接失败：{message}")
        finally:
            await self._cleanup()
            self._room = None
            self._devices = None
            self._input_capture = None
            self._output_player = None
            self._local_audio_track = None
            self._caption_segments = {"user": {}, "assistant": {}}
            self._loop = None
            self._stop_event = None

    def _bind_room_events(self, room: rtc.Room) -> None:
        room.on("connection_state_changed", self._on_connection_state_changed)
        room.on("participant_connected", self._on_participant_connected)
        room.on("participant_disconnected", self._on_participant_disconnected)
        room.on("transcription_received", self._on_transcription_received)
        room.on("data_received", self._on_data_received)
        room.on(
            "track_subscribed",
            lambda track, publication, participant: self._spawn(
                self._on_track_subscribed(track, publication, participant)
            ),
        )
        room.on(
            "track_unsubscribed",
            lambda track, publication, participant: self._spawn(
                self._on_track_unsubscribed(track, publication, participant)
            ),
        )

    def _spawn(self, coroutine: asyncio.Future | asyncio.Task | Any) -> None:
        task = asyncio.create_task(coroutine)
        task.add_done_callback(self._report_background_error)

    def _report_background_error(self, task: asyncio.Task[Any]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                self.failure_reported.emit(str(exc) or exc.__class__.__name__)

    def _sync_existing_participants(self) -> None:
        if self._room is None:
            return
        for participant in self._room.remote_participants.values():
            self._on_participant_connected(participant)

    def _apply_local_mute(self) -> None:
        if self._local_audio_track is None:
            return
        if self._muted:
            self._local_audio_track.mute()
        else:
            self._local_audio_track.unmute()

    def _is_agent_participant(self, participant: rtc.RemoteParticipant) -> bool:
        return (
            participant.kind == participant_pb2.ParticipantKind.PARTICIPANT_KIND_AGENT
            or participant.identity != self.session_config.user_identity
        )

    def _update_agent_presence(self, available: bool) -> None:
        if self._agent_present == available:
            return
        self._agent_present = available
        self.agent_availability_changed.emit(available)
        if available:
            self.state_changed.emit("ready", "AI 已接入会话，现在可以开始对话。")
        else:
            self.state_changed.emit("waiting_agent", "会话已连接，等待 AI 接入。")

    def _on_connection_state_changed(self, state: rtc.ConnectionState.ValueType) -> None:
        if state == rtc.ConnectionState.CONN_CONNECTED:
            self.state_changed.emit(
                "ready" if self._agent_present else "waiting_agent",
                "AI 已接入会话，现在可以开始对话。"
                if self._agent_present
                else "会话已连接，等待 AI 接入。",
            )
        elif state == rtc.ConnectionState.CONN_RECONNECTING:
            self.state_changed.emit("reconnecting", "LiveKit 连接波动，正在恢复。")
        elif state == rtc.ConnectionState.CONN_DISCONNECTED:
            if self._stop_event is not None and self._stop_event.is_set():
                self.state_changed.emit("idle", "语音会话已关闭。")
                return
            if not self._reconnect_emitted:
                self._reconnect_emitted = True
                self.state_changed.emit("reconnecting", "连接已断开，正在申请新的会话 token。")
                self.reconnect_requested.emit()

    def _on_participant_connected(self, participant: rtc.RemoteParticipant) -> None:
        if not self._is_agent_participant(participant):
            return
        self.system_message.emit(f"AI 已加入会话：{participant.identity}")
        self._update_agent_presence(True)

    def _on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        if not self._is_agent_participant(participant):
            return
        self.system_message.emit(f"AI 已离开会话：{participant.identity}")
        self._update_agent_presence(False)

    def _on_transcription_received(
        self,
        segments: list[rtc.TranscriptionSegment],
        participant: rtc.Participant | None,
        _publication: rtc.TrackPublication | None,
    ) -> None:
        identity = getattr(participant, "identity", "") if participant is not None else ""
        if identity == self.session_config.user_identity:
            role = "user"
        elif participant is not None and isinstance(participant, rtc.RemoteParticipant) and self._is_agent_participant(participant):
            role = "assistant"
        else:
            return

        bucket = self._caption_segments.setdefault(role, {})
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            segment_id = str(getattr(segment, "id", "") or "").strip()
            if not segment_id:
                start_time = float(getattr(segment, "start_time", 0.0) or 0.0)
                end_time = float(getattr(segment, "end_time", 0.0) or 0.0)
                segment_id = f"{start_time:.3f}:{end_time:.3f}:{len(bucket)}"
            bucket[segment_id] = (
                float(getattr(segment, "start_time", 0.0) or 0.0),
                text,
                bool(segment.final),
            )

        if not bucket:
            return

        ordered_segments = [item for _key, item in sorted(bucket.items(), key=lambda entry: (entry[1][0], entry[0]))]
        combined_text = " ".join(text for _start, text, _final in ordered_segments).strip()
        if not combined_text:
            return

        is_final = all(final for _start, _text, final in ordered_segments)
        self.live_caption_changed.emit(role, combined_text, is_final)
        if is_final:
            bucket.clear()

    def _on_data_received(self, packet: rtc.DataPacket) -> None:
        if packet.topic != "lk.system":
            return
        try:
            payload = json.loads(packet.data.decode("utf-8"))
        except Exception:
            self.system_message.emit(packet.data.decode("utf-8", errors="replace"))
            return
        if not isinstance(payload, dict):
            self.system_message.emit(json.dumps(payload, ensure_ascii=False))
            return
        message = str(payload.get("message") or payload.get("reason") or "").strip()
        if message:
            self.system_message.emit(message)
        else:
            self.system_message.emit(json.dumps(payload, ensure_ascii=False))

    async def _on_track_subscribed(
        self,
        track: rtc.Track,
        _publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if not self._is_agent_participant(participant):
            return
        if not isinstance(track, rtc.RemoteAudioTrack):
            return
        if self._output_player is None:
            return
        await self._output_player.add_track(track)
        self._update_agent_presence(True)

    async def _on_track_unsubscribed(
        self,
        track: rtc.Track,
        _publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if not self._is_agent_participant(participant):
            return
        if self._output_player is None:
            return
        await self._output_player.remove_track(track)

    async def _cleanup(self) -> None:
        if self._output_player is not None:
            with contextlib.suppress(Exception):
                await self._output_player.aclose()
        if self._input_capture is not None:
            with contextlib.suppress(Exception):
                await self._input_capture.aclose()
        if self._room is not None:
            with contextlib.suppress(Exception):
                await self._room.disconnect()
