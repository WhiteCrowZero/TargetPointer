from __future__ import annotations

import argparse
from dataclasses import dataclass
from types import SimpleNamespace

try:
    from serial.tools import list_ports
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pyserial. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

from pointer_host_logic import BBox, MatchResult, bbox_center, should_send_angle
from pointer_serial import PointerSerialClient, PointerSerialError
from pointer_vision_app import (
    STATE_LOCKED,
    STATE_LOST,
    STATE_REACQUIRING,
    STATE_SELECTING,
    AppState,
    DetectionCandidate,
    attempt_match,
    compute_servo_angle,
    detect_people,
    list_available_cameras,
    open_camera_capture,
    parse_camera_source,
    send_control_command,
)


@dataclass
class RuntimeSnapshot:
    frame: object
    tracking_state: str
    pending_detections: list[DetectionCandidate]
    tracked_bbox: BBox | None
    smoothed_target_center: tuple[float, float] | None
    output_angle: int | None
    missed_frames: int
    on_loss: str
    last_match: MatchResult | None
    last_match_success: bool
    last_detection_ran: bool
    serial_connected: bool
    serial_port: str | None
    camera_source: str | None
    camera_backend: str | None


def list_serial_ports() -> list[str]:
    ports = [port.device for port in list_ports.comports()]
    ports.sort()
    return ports


class PointerRuntime:
    def __init__(
        self,
        detector,
        model_name: str,
        camera_backend: str = "auto",
        yolo_confidence: float = 0.35,
        detect_every: int = 1,
        match_min_iou: float = 0.0,
        match_max_center_ratio: float = 2.2,
        match_max_area_change: float = 1.25,
        reacquire_center_ratio_multiplier: float = 1.8,
        reacquire_area_change_multiplier: float = 1.5,
        bbox_smooth_alpha: float = 0.35,
        reacquire_frames: int = 12,
        min_box_width: int = 40,
        min_box_height: int = 80,
        min_angle: int = 20,
        center_angle: int = 90,
        max_angle: int = 160,
        center_deadzone: int = 2,
        smooth_step: int = 4,
        angle_step_threshold: int = 2,
        on_loss: str = "stop",
        serial_baud: int = 115200,
        serial_timeout: float = 0.05,
        startup_timeout: float = 1.5,
        serial_response_timeout: float = 0.25,
        serial_idle_timeout: float = 0.05,
    ) -> None:
        self.detector = detector
        self.model_name = model_name
        self.camera_backend_preference = camera_backend
        self.serial_baud = serial_baud
        self.serial_timeout = serial_timeout
        self.startup_timeout = startup_timeout
        self.serial_response_timeout = serial_response_timeout
        self.serial_idle_timeout = serial_idle_timeout

        self.args = SimpleNamespace(
            min_angle=min_angle,
            center_angle=center_angle,
            max_angle=max_angle,
            center_deadzone=center_deadzone,
            smooth_step=smooth_step,
            angle_step_threshold=angle_step_threshold,
            match_min_iou=match_min_iou,
            match_max_center_ratio=match_max_center_ratio,
            match_max_area_change=match_max_area_change,
            reacquire_center_ratio_multiplier=reacquire_center_ratio_multiplier,
            reacquire_area_change_multiplier=reacquire_area_change_multiplier,
            bbox_smooth_alpha=bbox_smooth_alpha,
            reacquire_frames=reacquire_frames,
            yolo_confidence=yolo_confidence,
            detect_every=detect_every,
            min_box_width=min_box_width,
            min_box_height=min_box_height,
            on_loss=on_loss,
        )

        self.state = AppState(
            pending_detections=[],
            pending_selection=None,
            tracking_state=STATE_SELECTING,
            last_match=None,
            last_detection_ran=False,
            last_match_success=False,
        )
        self.capture = None
        self.camera_source: str | None = None
        self.camera_backend_name: str | None = None
        self.serial_client: PointerSerialClient | None = None
        self.serial_port: str | None = None
        self.tracked_bbox: BBox | None = None
        self.smoothed_target_center: tuple[float, float] | None = None
        self.last_output_angle: int | None = None
        self.missed_frames = 0
        self.frame_index = 0
        self.force_detection = False

    def list_cameras(self, max_index: int = 4) -> list[tuple[int, str, bool]]:
        return list_available_cameras(max_index, self.camera_backend_preference)

    def connect_serial(self, port: str) -> list[str]:
        self.disconnect_serial()
        self.serial_client = PointerSerialClient(port, self.serial_baud, timeout=self.serial_timeout)
        self.serial_port = port
        responses: list[str] = []
        try:
            if self.startup_timeout > 0:
                responses.extend(self.serial_client.read_startup(self.startup_timeout, self.serial_idle_timeout))
            responses.extend(
                send_control_command(
                    self.serial_client,
                    "CENTER",
                    response_timeout=self.serial_response_timeout,
                    idle_timeout=self.serial_idle_timeout,
                    require_response=True,
                )
            )
        except Exception:
            self.disconnect_serial()
            raise
        self.last_output_angle = self.args.center_angle
        return responses

    def disconnect_serial(self) -> None:
        if self.serial_client is None:
            return
        try:
            send_control_command(
                self.serial_client,
                "STOP",
                response_timeout=self.serial_response_timeout,
                idle_timeout=self.serial_idle_timeout,
                require_response=False,
            )
        except PointerSerialError:
            pass
        self.serial_client.close()
        self.serial_client = None
        self.serial_port = None

    def open_camera(self, source: str) -> tuple[str, str]:
        self.close_camera()
        camera_source = parse_camera_source(source)
        self.capture, self.camera_backend_name = open_camera_capture(camera_source, self.camera_backend_preference)
        self.camera_source = source
        self.frame_index = 0
        self.force_detection = True
        return source, self.camera_backend_name

    def close_camera(self) -> None:
        if self.capture is not None:
            self.capture.release()
        self.capture = None
        self.camera_source = None
        self.camera_backend_name = None

    def clear_tracking(self) -> None:
        self.tracked_bbox = None
        self.smoothed_target_center = None
        self.state.pending_selection = None
        self.state.last_match = None
        self.state.last_match_success = False
        self.state.tracking_state = STATE_SELECTING
        self.missed_frames = 0

    def center_device(self) -> list[str]:
        responses: list[str] = []
        if self.serial_client is not None:
            responses = send_control_command(
                self.serial_client,
                "CENTER",
                response_timeout=self.serial_response_timeout,
                idle_timeout=self.serial_idle_timeout,
                require_response=True,
            )
        self.last_output_angle = self.args.center_angle
        self.clear_tracking()
        return responses

    def stop_device(self) -> list[str]:
        responses: list[str] = []
        if self.serial_client is not None:
            responses = send_control_command(
                self.serial_client,
                "STOP",
                response_timeout=self.serial_response_timeout,
                idle_timeout=self.serial_idle_timeout,
                require_response=True,
            )
        self.clear_tracking()
        return responses

    def request_redetect(self) -> None:
        self.force_detection = True

    def select_target_at(self, point_x: int, point_y: int) -> bool:
        for detection in self.state.pending_detections:
            if detection.contains(point_x, point_y):
                self.state.pending_selection = detection.bbox
                return True
        return False

    def select_target_bbox(self, bbox: BBox) -> None:
        self.state.pending_selection = bbox

    def _loss_action(self) -> list[str]:
        responses: list[str] = []
        if self.args.on_loss == "center":
            self.last_output_angle = self.args.center_angle
        if self.serial_client is not None:
            loss_command = "CENTER" if self.args.on_loss == "center" else "STOP"
            responses = send_control_command(
                self.serial_client,
                loss_command,
                response_timeout=self.serial_response_timeout,
                idle_timeout=self.serial_idle_timeout,
                require_response=True,
            )
        self.tracked_bbox = None
        self.smoothed_target_center = None
        self.state.tracking_state = STATE_LOST
        self.state.last_match = None
        self.state.last_match_success = False
        return responses

    def _run_detection_cycle(self, frame) -> None:
        self.state.pending_detections = detect_people(
            self.detector,
            frame,
            confidence_threshold=self.args.yolo_confidence,
            min_box_width=self.args.min_box_width,
            min_box_height=self.args.min_box_height,
        )

    def process_next_frame(self) -> RuntimeSnapshot:
        if self.capture is None:
            raise RuntimeError("Camera is not open")

        ok, frame = self.capture.read()
        if not ok:
            raise RuntimeError("Camera frame read failed")

        self.frame_index += 1
        run_detection = self.force_detection or self.frame_index == 1 or self.frame_index % self.args.detect_every == 0
        self.state.last_detection_ran = run_detection
        self.force_detection = False

        if run_detection:
            self._run_detection_cycle(frame)

        just_selected = False
        if self.state.pending_selection is not None:
            self.tracked_bbox = self.state.pending_selection
            self.smoothed_target_center = bbox_center(self.tracked_bbox)
            self.state.pending_selection = None
            self.state.tracking_state = STATE_LOCKED
            self.state.last_match = None
            self.state.last_match_success = True
            self.missed_frames = 0
            just_selected = True

        if self.tracked_bbox is not None and run_detection and not just_selected:
            match, used_relaxed_match = attempt_match(
                self.tracked_bbox,
                [candidate.bbox for candidate in self.state.pending_detections],
                self.args,
            )
            self.state.last_match = match
            self.state.last_match_success = match is not None

            if match is not None:
                self.tracked_bbox = self.state.pending_detections[match.index].bbox
                self.smoothed_target_center = self._smooth_center(self.tracked_bbox)
                self.missed_frames = 0
                self.state.tracking_state = STATE_REACQUIRING if used_relaxed_match else STATE_LOCKED
            else:
                self.missed_frames += 1
                self.state.tracking_state = STATE_REACQUIRING
                if self.missed_frames >= self.args.reacquire_frames:
                    self._loss_action()

        if self.tracked_bbox is not None and self.smoothed_target_center is not None and (just_selected or self.state.last_match_success):
            output_angle = compute_servo_angle(self.smoothed_target_center, frame.shape[1], self.last_output_angle, self.args)
            should_send = should_send_angle(self.last_output_angle, output_angle, self.args.angle_step_threshold)
            self.last_output_angle = output_angle
            if self.serial_client is not None:
                if just_selected or should_send:
                    send_control_command(
                        self.serial_client,
                        f"ANGLE:{output_angle}",
                        response_timeout=self.serial_response_timeout,
                        idle_timeout=self.serial_idle_timeout,
                        require_response=True,
                    )

        if self.tracked_bbox is None and self.state.tracking_state != STATE_LOST:
            self.state.tracking_state = STATE_SELECTING

        return RuntimeSnapshot(
            frame=frame,
            tracking_state=self.state.tracking_state,
            pending_detections=list(self.state.pending_detections),
            tracked_bbox=self.tracked_bbox,
            smoothed_target_center=self.smoothed_target_center,
            output_angle=self.last_output_angle,
            missed_frames=self.missed_frames,
            on_loss=self.args.on_loss,
            last_match=self.state.last_match,
            last_match_success=self.state.last_match_success,
            last_detection_ran=self.state.last_detection_ran,
            serial_connected=self.serial_client is not None,
            serial_port=self.serial_port,
            camera_source=self.camera_source,
            camera_backend=self.camera_backend_name,
        )

    def _smooth_center(self, tracked_bbox: BBox) -> tuple[float, float]:
        from pointer_host_logic import smooth_center

        return smooth_center(
            self.smoothed_target_center,
            bbox_center(tracked_bbox),
            self.args.bbox_smooth_alpha,
        )
