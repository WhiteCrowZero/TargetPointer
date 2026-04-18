#!/usr/bin/env python3

import argparse
import sys
from dataclasses import dataclass

try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: opencv-python. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

try:
    from ultralytics import YOLO
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: ultralytics. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

import serial

from pointer_host_logic import (
    BBox,
    MatchResult,
    apply_deadzone,
    bbox_center,
    hold_angle_if_within_threshold,
    map_center_to_angle,
    match_target_bbox,
    should_send_angle,
    should_stop_for_loss,
    smooth_angle,
    smooth_angle_adaptive,
    smooth_center,
)
from pointer_serial import PointerSerialClient, PointerSerialError


WINDOW_NAME = "TargetPointer"
WINDOWS_CAMERA_BACKENDS = (
    ("msmf", "CAP_MSMF"),
    ("dshow", "CAP_DSHOW"),
)
STATE_SELECTING = "selecting"
STATE_LOCKED = "locked"
STATE_REACQUIRING = "reacquiring"
STATE_LOST = "lost"


@dataclass
class DetectionCandidate:
    bbox: BBox
    confidence: float

    def contains(self, point_x: int, point_y: int) -> bool:
        x, y, width, height = self.bbox
        return x <= point_x <= x + width and y <= point_y <= y + height


@dataclass
class AppState:
    pending_detections: list[DetectionCandidate]
    pending_selection: BBox | None
    tracking_state: str
    last_match: MatchResult | None
    last_detection_ran: bool
    last_match_success: bool


def parse_camera_source(raw_source: str):
    return int(raw_source) if raw_source.isdigit() else raw_source


def resolve_camera_backend_constant(backend_name: str) -> int | None:
    if backend_name in ("auto", "any"):
        return cv2.CAP_ANY

    attribute_name = dict(WINDOWS_CAMERA_BACKENDS).get(backend_name)
    if attribute_name is None:
        raise ValueError(f"Unsupported camera backend: {backend_name}")

    backend_constant = getattr(cv2, attribute_name, None)
    if backend_constant is None:
        raise SystemExit(f"OpenCV build does not provide {attribute_name}. Try --camera-backend any.")
    return backend_constant


def build_camera_candidates(camera_source, backend_name: str) -> list[tuple[object, int, str]]:
    if backend_name != "auto":
        backend_constant = resolve_camera_backend_constant(backend_name)
        return [(camera_source, backend_constant, backend_name)]

    if isinstance(camera_source, int) and sys.platform.startswith("win"):
        candidates: list[tuple[object, int, str]] = []
        for candidate_name, attribute_name in WINDOWS_CAMERA_BACKENDS:
            backend_constant = getattr(cv2, attribute_name, None)
            if backend_constant is not None:
                candidates.append((camera_source, backend_constant, candidate_name))
        candidates.append((camera_source, cv2.CAP_ANY, "any"))
        return candidates

    return [(camera_source, cv2.CAP_ANY, "any")]


def capture_reads_frame(capture) -> bool:
    ok, _frame = capture.read()
    return bool(ok)


def try_open_camera_source(camera_source, backend_name: str) -> tuple[object | None, str | None, list[str]]:
    errors: list[str] = []

    for candidate_source, backend_constant, candidate_name in build_camera_candidates(camera_source, backend_name):
        capture = cv2.VideoCapture(candidate_source, backend_constant)
        if capture.isOpened():
            return capture, candidate_name, errors
        capture.release()
        errors.append(candidate_name)

    return None, None, errors


def open_camera_capture(camera_source, backend_name: str):
    capture, selected_backend, errors = try_open_camera_source(camera_source, backend_name)
    if capture is not None and selected_backend is not None:
        return capture, selected_backend
    attempted = ", ".join(errors) if errors else backend_name
    raise SystemExit(
        f"Failed to open camera source {camera_source!r}. Attempted backends: {attempted}. "
        "On Windows, try --list-cameras, --camera-backend msmf, or a different --camera index."
    )


def list_available_cameras(max_index: int, backend_name: str) -> list[tuple[int, str, bool]]:
    results: list[tuple[int, str, bool]] = []
    for camera_index in range(max_index + 1):
        capture, selected_backend, _errors = try_open_camera_source(camera_index, backend_name)
        if capture is None or selected_backend is None:
            continue

        read_ok = capture_reads_frame(capture)
        capture.release()
        results.append((camera_index, selected_backend, read_ok))

    return results


def select_target_bbox(frame) -> BBox | None:
    x, y, width, height = cv2.selectROI(WINDOW_NAME, frame, fromCenter=False, showCrosshair=True)
    if width <= 0 or height <= 0:
        return None
    return int(x), int(y), int(width), int(height)


def detect_people(
    detector: YOLO,
    frame,
    confidence_threshold: float,
    min_box_width: int,
    min_box_height: int,
) -> list[DetectionCandidate]:
    results = detector.predict(frame, classes=[0], conf=confidence_threshold, verbose=False)
    detections: list[DetectionCandidate] = []

    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            x1, y1, x2, y2 = [int(round(value)) for value in box.xyxy[0].tolist()]
            width = max(0, x2 - x1)
            height = max(0, y2 - y1)
            if width < min_box_width or height < min_box_height:
                continue
            detections.append(DetectionCandidate((x1, y1, width, height), float(box.conf[0])))

    detections.sort(key=lambda item: item.confidence, reverse=True)
    return detections


def handle_mouse(event: int, x: int, y: int, flags: int, state: AppState) -> None:
    del flags
    if event != cv2.EVENT_LBUTTONDOWN:
        return

    for detection in state.pending_detections:
        if detection.contains(x, y):
            state.pending_selection = detection.bbox
            return


def format_match_text(match: MatchResult | None, matched: bool) -> str:
    if match is None:
        return "match=none"
    status = "ok" if matched else "candidate"
    return (
        f"match={status} score={match.score:.2f} "
        f"iou={match.iou:.2f} center={match.center_ratio:.2f} area={match.area_change:.2f}"
    )


def draw_overlay(
    frame,
    tracking_state: str,
    tracked_bbox: BBox | None,
    smoothed_target_center: tuple[float, float] | None,
    pending_detections: list[DetectionCandidate],
    target_angle: int | None,
    sent_angle: int | None,
    missed_frames: int,
    on_loss: str,
    last_match: MatchResult | None,
    last_match_success: bool,
    last_detection_ran: bool,
) -> None:
    height, width = frame.shape[:2]
    frame_center_x = width // 2

    cv2.line(frame, (frame_center_x, 0), (frame_center_x, height), (255, 255, 0), 1)
    cv2.putText(frame, "Detection-driven person pointing", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
    cv2.putText(frame, "Keys: d detect, r ROI, c center, x stop, q quit", (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(frame, f"state={tracking_state}", (10, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    cv2.putText(frame, f"detections={len(pending_detections)} detect_ran={int(last_detection_ran)}", (10, 96), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
    cv2.putText(frame, f"missed_frames={missed_frames} on_loss={on_loss}", (10, 118), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
    cv2.putText(frame, format_match_text(last_match, last_match_success), (10, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    if target_angle is not None or sent_angle is not None:
        target_text = "—" if target_angle is None else str(target_angle)
        output_text = "—" if sent_angle is None else str(sent_angle)
        cv2.putText(
            frame,
            f"target_angle={target_text} output_angle={output_text}",
            (10, 162),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
        )

    for index, detection in enumerate(pending_detections, start=1):
        x, y, box_width, box_height = detection.bbox
        cv2.rectangle(frame, (x, y), (x + box_width, y + box_height), (0, 165, 255), 2)
        cv2.putText(
            frame,
            f"person#{index} {detection.confidence:.2f}",
            (x, max(20, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 165, 255),
            1,
        )

    if tracked_bbox is None:
        return

    x, y, box_width, box_height = tracked_bbox
    cv2.rectangle(frame, (x, y), (x + box_width, y + box_height), (0, 255, 0), 2)
    target_center = bbox_center(tracked_bbox)
    cv2.circle(frame, (int(target_center[0]), int(target_center[1])), 4, (0, 255, 0), -1)

    if smoothed_target_center is not None:
        cv2.circle(frame, (int(smoothed_target_center[0]), int(smoothed_target_center[1])), 4, (255, 0, 255), -1)


def send_control_command(
    serial_client: PointerSerialClient,
    command: str,
    response_timeout: float,
    idle_timeout: float,
    require_response: bool = True,
) -> list[str]:
    return serial_client.send(
        command,
        response_timeout=response_timeout,
        idle_timeout=idle_timeout,
        require_response=require_response,
    )


def compute_target_servo_angle(
    smoothed_target_center: tuple[float, float],
    frame_width: int,
    args: argparse.Namespace,
    current_output_angle: int | None = None,
) -> int:
    raw_angle = map_center_to_angle(
        smoothed_target_center[0],
        frame_width,
        args.min_angle,
        args.center_angle,
        args.max_angle,
    )
    deadzoned_angle = apply_deadzone(raw_angle, args.center_angle, args.center_deadzone)
    return hold_angle_if_within_threshold(current_output_angle, deadzoned_angle, args.angle_hold_threshold)


def compute_servo_angle(
    smoothed_target_center: tuple[float, float],
    frame_width: int,
    last_sent_angle: int | None,
    args: argparse.Namespace,
) -> int:
    target_angle = compute_target_servo_angle(smoothed_target_center, frame_width, args, current_output_angle=last_sent_angle)
    return smooth_angle_adaptive(
        last_sent_angle,
        target_angle,
        args.center_angle,
        args.angle_small_error_threshold,
        args.angle_medium_error_threshold,
        args.angle_small_step,
        args.angle_medium_step,
        args.angle_large_step,
    )


def attempt_match(
    tracked_bbox: BBox,
    candidate_bboxes: list[BBox],
    args: argparse.Namespace,
) -> tuple[MatchResult | None, bool]:
    strict_match = match_target_bbox(
        tracked_bbox,
        candidate_bboxes,
        min_iou=args.match_min_iou,
        max_center_ratio=args.match_max_center_ratio,
        max_area_change=args.match_max_area_change,
    )
    if strict_match is not None:
        return strict_match, False

    relaxed_match = match_target_bbox(
        tracked_bbox,
        candidate_bboxes,
        min_iou=0.0,
        max_center_ratio=args.match_max_center_ratio * args.reacquire_center_ratio_multiplier,
        max_area_change=args.match_max_area_change * args.reacquire_area_change_multiplier,
    )
    return relaxed_match, relaxed_match is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host app for fixed-camera person pointing with detection-driven target maintenance."
    )
    parser.add_argument("--port", help="Serial port, for example COM5.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--camera", default="0", help="Camera index or URL. Default: 0.")
    parser.add_argument(
        "--camera-backend",
        choices=("auto", "any", "dshow", "msmf"),
        default="auto",
        help="Camera backend. On Windows, auto tries msmf, dshow, then any.",
    )
    parser.add_argument("--list-cameras", action="store_true", help="Probe Windows camera indices and exit.")
    parser.add_argument("--camera-scan-max-index", type=int, default=4, help="Highest camera index to probe with --list-cameras.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path or model name.")
    parser.add_argument("--tracking-mode", choices=("detection",), default="detection", help="Detection-driven tracking mode.")
    parser.add_argument("--yolo-confidence", type=float, default=0.35, help="Minimum confidence for YOLO person detections.")
    parser.add_argument("--detect-every", type=int, default=1, help="Run YOLO every N frames.")
    parser.add_argument("--match-min-iou", type=float, default=0.0, help="Reject matches with IoU below this threshold.")
    parser.add_argument("--match-max-center-ratio", type=float, default=2.2, help="Reject matches when center drift exceeds this ratio.")
    parser.add_argument("--match-max-area-change", type=float, default=1.25, help="Reject matches when relative area change exceeds this ratio.")
    parser.add_argument("--reacquire-center-ratio-multiplier", type=float, default=1.8, help="Loosen center drift tolerance by this factor during short-term reacquire.")
    parser.add_argument("--reacquire-area-change-multiplier", type=float, default=1.5, help="Loosen area change tolerance by this factor during short-term reacquire.")
    parser.add_argument("--bbox-smooth-alpha", type=float, default=0.35, help="Exponential smoothing factor for target center updates.")
    parser.add_argument("--reacquire-frames", type=int, default=12, help="Allow this many missed detection cycles before declaring loss.")
    parser.add_argument("--min-box-width", type=int, default=40, help="Ignore detections narrower than this many pixels.")
    parser.add_argument("--min-box-height", type=int, default=80, help="Ignore detections shorter than this many pixels.")
    parser.add_argument("--timeout", type=float, default=0.05, help="Per-read serial timeout in seconds.")
    parser.add_argument("--startup-timeout", type=float, default=1.5, help="Wait this long for boot logs before sending commands.")
    parser.add_argument("--serial-response-timeout", type=float, default=0.25, help="Total wait time for firmware replies.")
    parser.add_argument("--serial-idle-timeout", type=float, default=0.05, help="Stop reading after this serial idle gap.")
    parser.add_argument("--min-angle", type=int, default=20)
    parser.add_argument("--center-angle", type=int, default=90)
    parser.add_argument("--max-angle", type=int, default=160)
    parser.add_argument("--center-deadzone", type=int, default=2, help="Keep commands at center when within this many degrees.")
    parser.add_argument("--smooth-step", type=int, default=4, help="Legacy fixed-step option. Kept for compatibility but not used by the default adaptive controller.")
    parser.add_argument("--angle-small-error-threshold", type=int, default=4, help="Use the small step when angle error is within this range.")
    parser.add_argument("--angle-medium-error-threshold", type=int, default=18, help="Use the medium step when angle error is within this range.")
    parser.add_argument("--angle-small-step", type=int, default=1, help="Max per-update angle change for small errors.")
    parser.add_argument("--angle-medium-step", type=int, default=3, help="Max per-update angle change for medium errors.")
    parser.add_argument("--angle-large-step", type=int, default=6, help="Max per-update angle change for large errors.")
    parser.add_argument("--angle-hold-threshold", type=int, default=2, help="Ignore target-angle jitter within this many degrees of the current output angle.")
    parser.add_argument("--angle-step-threshold", type=int, default=2, help="Only resend angle when the change is at least this many degrees.")
    parser.add_argument("--on-loss", choices=("stop", "center"), default="stop", help="Behavior after continuous target loss.")
    parser.add_argument("--verbose", action="store_true", help="Print host-side state transitions and commands.")
    args = parser.parse_args()

    if args.detect_every <= 0:
        parser.error("--detect-every must be positive")
    if args.angle_small_error_threshold < 0:
        parser.error("--angle-small-error-threshold must be non-negative")
    if args.angle_medium_error_threshold < args.angle_small_error_threshold:
        parser.error("--angle-medium-error-threshold must be >= --angle-small-error-threshold")
    if min(args.angle_small_step, args.angle_medium_step, args.angle_large_step) <= 0:
        parser.error("--angle-small-step, --angle-medium-step, and --angle-large-step must be positive")
    if not args.angle_small_step <= args.angle_medium_step <= args.angle_large_step:
        parser.error("--angle steps must satisfy small <= medium <= large")
    if args.angle_hold_threshold < 0:
        parser.error("--angle-hold-threshold must be non-negative")

    if args.list_cameras:
        camera_results = list_available_cameras(args.camera_scan_max_index, args.camera_backend)
        if not camera_results:
            print(
                f"No camera sources opened successfully in range 0..{args.camera_scan_max_index}. "
                "Try a different backend with --camera-backend msmf or check whether another app is holding the camera.",
                file=sys.stderr,
            )
            return 1

        for camera_index, backend_name, read_ok in camera_results:
            status = "ok" if read_ok else "opened_no_frame"
            print(f"camera={camera_index} backend={backend_name} status={status}")
        return 0

    if not args.port:
        parser.error("--port is required unless --list-cameras is used")

    try:
        detector = YOLO(args.model)
    except Exception as exc:
        print(f"Failed to load YOLO model: {exc}", file=sys.stderr)
        return 1

    try:
        serial_client = PointerSerialClient(args.port, args.baud, timeout=args.timeout)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    try:
        capture, camera_backend = open_camera_capture(parse_camera_source(args.camera), args.camera_backend)
    except SystemExit as exc:
        serial_client.close()
        print(exc, file=sys.stderr)
        return 1

    state = AppState(
        pending_detections=[],
        pending_selection=None,
        tracking_state=STATE_SELECTING,
        last_match=None,
        last_detection_ran=False,
        last_match_success=False,
    )
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW_NAME, handle_mouse, state)

    tracked_bbox: BBox | None = None
    smoothed_target_center: tuple[float, float] | None = None
    last_sent_angle: int | None = None
    missed_frames = 0
    frame_index = 0
    force_detection = False
    target_angle: int | None = None

    try:
        if args.verbose:
            print(f"Camera opened: source={args.camera} backend={camera_backend}")
        if args.startup_timeout > 0:
            serial_client.read_startup(args.startup_timeout, args.serial_idle_timeout)
        send_control_command(
            serial_client,
            "CENTER",
            response_timeout=args.serial_response_timeout,
            idle_timeout=args.serial_idle_timeout,
            require_response=True,
        )
        last_sent_angle = args.center_angle

        while True:
            ok, frame = capture.read()
            if not ok:
                print("Camera frame read failed.", file=sys.stderr)
                return 1

            frame_index += 1
            run_detection = force_detection or frame_index == 1 or frame_index % args.detect_every == 0
            state.last_detection_ran = run_detection
            force_detection = False

            if run_detection:
                state.pending_detections = detect_people(
                    detector,
                    frame,
                    confidence_threshold=args.yolo_confidence,
                    min_box_width=args.min_box_width,
                    min_box_height=args.min_box_height,
                )

            just_selected = False
            if state.pending_selection is not None:
                tracked_bbox = state.pending_selection
                smoothed_target_center = bbox_center(tracked_bbox)
                state.pending_selection = None
                state.tracking_state = STATE_LOCKED
                state.last_match = None
                state.last_match_success = True
                missed_frames = 0
                just_selected = True
                if args.verbose:
                    print(f"Selected target bbox={tracked_bbox}")

            if tracked_bbox is not None and run_detection and not just_selected:
                match, used_relaxed_match = attempt_match(
                    tracked_bbox,
                    [candidate.bbox for candidate in state.pending_detections],
                    args,
                )
                state.last_match = match
                state.last_match_success = match is not None

                if match is not None:
                    tracked_bbox = state.pending_detections[match.index].bbox
                    smoothed_target_center = smooth_center(
                        smoothed_target_center,
                        bbox_center(tracked_bbox),
                        args.bbox_smooth_alpha,
                    )
                    missed_frames = 0
                    state.tracking_state = STATE_REACQUIRING if used_relaxed_match else STATE_LOCKED
                else:
                    missed_frames += 1
                    state.tracking_state = STATE_REACQUIRING
                    if should_stop_for_loss(missed_frames, args.reacquire_frames):
                        loss_command = "CENTER" if args.on_loss == "center" else "STOP"
                        responses = send_control_command(
                            serial_client,
                            loss_command,
                            response_timeout=args.serial_response_timeout,
                            idle_timeout=args.serial_idle_timeout,
                            require_response=True,
                        )
                        tracked_bbox = None
                        smoothed_target_center = None
                        state.tracking_state = STATE_LOST
                        state.last_match = None
                        state.last_match_success = False
                        if loss_command == "CENTER":
                            last_sent_angle = args.center_angle
                        if args.verbose and responses:
                            print(responses[-1])
                        target_angle = args.center_angle if loss_command == "CENTER" else None

            if tracked_bbox is not None and smoothed_target_center is not None and (just_selected or state.last_match_success):
                target_angle = compute_target_servo_angle(
                    smoothed_target_center,
                    frame.shape[1],
                    args,
                    current_output_angle=last_sent_angle,
                )
                angle = compute_servo_angle(smoothed_target_center, frame.shape[1], last_sent_angle, args)
                if should_send_angle(last_sent_angle, angle, args.angle_step_threshold):
                    responses = send_control_command(
                        serial_client,
                        f"ANGLE:{angle}",
                        response_timeout=args.serial_response_timeout,
                        idle_timeout=args.serial_idle_timeout,
                        require_response=True,
                    )
                    last_sent_angle = angle
                    if args.verbose:
                        print(f"target_angle={target_angle} output_angle={angle}")
                        if responses:
                            print(responses[-1])

            if tracked_bbox is None and state.tracking_state != STATE_LOST:
                state.tracking_state = STATE_SELECTING
                if state.pending_selection is None:
                    target_angle = None

            draw_overlay(
                frame,
                tracking_state=state.tracking_state,
                tracked_bbox=tracked_bbox,
                smoothed_target_center=smoothed_target_center,
                pending_detections=state.pending_detections,
                target_angle=target_angle,
                sent_angle=last_sent_angle,
                missed_frames=missed_frames,
                on_loss=args.on_loss,
                last_match=state.last_match,
                last_match_success=state.last_match_success,
                last_detection_ran=state.last_detection_ran,
            )
            cv2.imshow(WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            if key == ord("d"):
                force_detection = True
                if args.verbose:
                    print("Forced detection on next frame")
            elif key == ord("r"):
                bbox = select_target_bbox(frame)
                if bbox is None:
                    continue
                state.pending_selection = bbox
                if args.verbose:
                    print(f"Queued manual bbox={bbox}")
            elif key == ord("c"):
                responses = send_control_command(
                    serial_client,
                    "CENTER",
                    response_timeout=args.serial_response_timeout,
                    idle_timeout=args.serial_idle_timeout,
                    require_response=True,
                )
                tracked_bbox = None
                smoothed_target_center = None
                state.tracking_state = STATE_SELECTING
                state.last_match = None
                state.last_match_success = False
                missed_frames = 0
                last_sent_angle = args.center_angle
                if args.verbose and responses:
                    print(responses[-1])
            elif key == ord("x"):
                responses = send_control_command(
                    serial_client,
                    "STOP",
                    response_timeout=args.serial_response_timeout,
                    idle_timeout=args.serial_idle_timeout,
                    require_response=True,
                )
                tracked_bbox = None
                smoothed_target_center = None
                state.tracking_state = STATE_SELECTING
                state.last_match = None
                state.last_match_success = False
                missed_frames = 0
                if args.verbose and responses:
                    print(responses[-1])

    except (serial.SerialException, PointerSerialError) as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            send_control_command(
                serial_client,
                "STOP",
                response_timeout=args.serial_response_timeout,
                idle_timeout=args.serial_idle_timeout,
                require_response=False,
            )
        except (serial.SerialException, PointerSerialError):
            pass
        capture.release()
        serial_client.close()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
