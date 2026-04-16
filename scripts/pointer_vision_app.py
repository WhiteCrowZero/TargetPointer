#!/usr/bin/env python3

import argparse
import sys

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

from pointer_host_logic import apply_deadzone, map_center_to_angle, should_send_angle, should_stop_for_loss, smooth_angle
from pointer_serial import PointerSerialClient, PointerSerialError


WINDOW_NAME = "TargetPointer"


class TrackedTarget:
    def __init__(self, bbox: tuple[int, int, int, int]) -> None:
        self.bbox = bbox

    @property
    def center_x(self) -> float:
        x, _, w, _ = self.bbox
        return x + w / 2.0

    @property
    def center_y(self) -> float:
        _, y, _, h = self.bbox
        return y + h / 2.0


class DetectionCandidate:
    def __init__(self, bbox: tuple[int, int, int, int], confidence: float) -> None:
        self.bbox = bbox
        self.confidence = confidence

    def contains(self, point_x: int, point_y: int) -> bool:
        x, y, w, h = self.bbox
        return x <= point_x <= x + w and y <= point_y <= y + h


class AppState:
    def __init__(self) -> None:
        self.pending_detections: list[DetectionCandidate] = []
        self.pending_selection: tuple[int, int, int, int] | None = None


def parse_camera_source(raw_source: str):
    return int(raw_source) if raw_source.isdigit() else raw_source


def create_tracker():
    if hasattr(cv2, "TrackerMIL_create"):
        return cv2.TrackerMIL_create()
    raise SystemExit("OpenCV tracker API is unavailable. Install a build that provides TrackerMIL.")


def select_target_bbox(frame) -> tuple[int, int, int, int] | None:
    x, y, w, h = cv2.selectROI(WINDOW_NAME, frame, fromCenter=False, showCrosshair=True)
    if w <= 0 or h <= 0:
        return None
    return int(x), int(y), int(w), int(h)


def build_tracked_target(bbox: tuple[int, int, int, int]) -> TrackedTarget | None:
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return None
    return TrackedTarget((x, y, w, h))


def detect_people(detector: YOLO, frame, confidence_threshold: float) -> list[DetectionCandidate]:
    results = detector.predict(frame, classes=[0], conf=confidence_threshold, verbose=False)
    detections: list[DetectionCandidate] = []

    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            x1, y1, x2, y2 = [int(round(value)) for value in box.xyxy[0].tolist()]
            width = max(0, x2 - x1)
            height = max(0, y2 - y1)
            if width == 0 or height == 0:
                continue
            detections.append(DetectionCandidate((x1, y1, width, height), float(box.conf[0])))

    detections.sort(key=lambda item: item.confidence, reverse=True)
    return detections


def update_tracker(tracker, frame) -> TrackedTarget | None:
    ok, raw_bbox = tracker.update(frame)
    if not ok:
        return None

    x, y, w, h = [int(round(value)) for value in raw_bbox]
    return build_tracked_target((x, y, w, h))


def start_tracking(frame, bbox: tuple[int, int, int, int]):
    tracker = create_tracker()
    tracker.init(frame, bbox)
    tracked_target = build_tracked_target(bbox)
    return tracker, tracked_target


def handle_mouse(event: int, x: int, y: int, flags: int, state: AppState) -> None:
    del flags
    if event != cv2.EVENT_LBUTTONDOWN:
        return

    for detection in state.pending_detections:
        if detection.contains(x, y):
            state.pending_selection = detection.bbox
            return


def draw_overlay(
    frame,
    tracked_target: TrackedTarget | None,
    pending_detections: list[DetectionCandidate],
    sent_angle: int | None,
    tracking_active: bool,
    missed_frames: int,
    on_loss: str,
) -> None:
    height, width = frame.shape[:2]
    center_x = width // 2

    cv2.line(frame, (center_x, 0), (center_x, height), (255, 255, 0), 1)
    cv2.putText(frame, "Click a YOLO person box to start tracking", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
    cv2.putText(frame, "Keys: d detect, r ROI, c center, x stop, q quit", (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    status = "tracking" if tracking_active and tracked_target is not None else "waiting_selection"
    if tracking_active and tracked_target is None:
        status = "target_lost"
    cv2.putText(frame, f"status={status}", (10, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    cv2.putText(frame, f"missed_frames={missed_frames}", (10, 96), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)
    cv2.putText(frame, f"on_loss={on_loss}", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

    if sent_angle is not None:
        cv2.putText(frame, f"angle={sent_angle}", (10, 144), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

    for index, detection in enumerate(pending_detections, start=1):
        x, y, w, h = detection.bbox
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 165, 255), 2)
        cv2.putText(
            frame,
            f"person#{index} {detection.confidence:.2f}",
            (x, max(20, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 165, 255),
            1,
        )

    if tracked_target is None:
        return

    x, y, w, h = tracked_target.bbox
    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.circle(frame, (int(tracked_target.center_x), int(tracked_target.center_y)), 4, (0, 255, 0), -1)


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Host app for fixed-camera person pointing with YOLO-assisted target initialization."
    )
    parser.add_argument("--port", required=True, help="Serial port, for example COM5.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--camera", default="0", help="Camera index or URL. Default: 0.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path or model name.")
    parser.add_argument("--yolo-confidence", type=float, default=0.35, help="Minimum confidence for YOLO person detections.")
    parser.add_argument("--detect-interval", type=int, default=12, help="Run YOLO every N frames while waiting for target selection.")
    parser.add_argument("--timeout", type=float, default=0.05, help="Per-read serial timeout in seconds.")
    parser.add_argument("--startup-timeout", type=float, default=1.5, help="Wait this long for boot logs before sending commands.")
    parser.add_argument("--serial-response-timeout", type=float, default=0.25, help="Total wait time for firmware replies.")
    parser.add_argument("--serial-idle-timeout", type=float, default=0.05, help="Stop reading after this serial idle gap.")
    parser.add_argument("--min-angle", type=int, default=20)
    parser.add_argument("--center-angle", type=int, default=90)
    parser.add_argument("--max-angle", type=int, default=160)
    parser.add_argument("--center-deadzone", type=int, default=2, help="Keep commands at center when within this many degrees.")
    parser.add_argument("--smooth-step", type=int, default=4, help="Limit each update to this many degrees. Use 0 to disable.")
    parser.add_argument("--angle-step-threshold", type=int, default=2, help="Only resend angle when the change is at least this many degrees.")
    parser.add_argument("--lost-target-hold-frames", type=int, default=5, help="Execute the loss command after this many consecutive missed frames.")
    parser.add_argument("--on-loss", choices=("stop", "center"), default="stop", help="Behavior after continuous target loss.")
    parser.add_argument("--verbose", action="store_true", help="Print host-side state transitions and commands.")
    args = parser.parse_args()

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

    capture = cv2.VideoCapture(parse_camera_source(args.camera))
    if not capture.isOpened():
        serial_client.close()
        print("Failed to open camera source.", file=sys.stderr)
        return 1

    state = AppState()
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW_NAME, handle_mouse, state)

    tracker = None
    tracked_target: TrackedTarget | None = None
    last_sent_angle: int | None = None
    missed_frames = 0
    tracking_active = False
    frame_index = 0

    try:
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

            if tracker is None and (frame_index == 1 or frame_index % max(1, args.detect_interval) == 0):
                state.pending_detections = detect_people(detector, frame, args.yolo_confidence)

            if state.pending_selection is not None:
                tracker, tracked_target = start_tracking(frame, state.pending_selection)
                state.pending_selection = None
                state.pending_detections = []
                tracking_active = True
                missed_frames = 0
                if args.verbose and tracked_target is not None:
                    print(f"Selected YOLO person bbox={tracked_target.bbox}")

            if tracker is not None:
                tracked_target = update_tracker(tracker, frame)
                if tracked_target is not None:
                    missed_frames = 0
                    tracking_active = True
                    raw_angle = map_center_to_angle(
                        tracked_target.center_x,
                        frame.shape[1],
                        args.min_angle,
                        args.center_angle,
                        args.max_angle,
                    )
                    deadzoned_angle = apply_deadzone(raw_angle, args.center_angle, args.center_deadzone)
                    angle = smooth_angle(last_sent_angle, deadzoned_angle, args.smooth_step)
                    if should_send_angle(last_sent_angle, angle, args.angle_step_threshold):
                        responses = send_control_command(
                            serial_client,
                            f"ANGLE:{angle}",
                            response_timeout=args.serial_response_timeout,
                            idle_timeout=args.serial_idle_timeout,
                            require_response=True,
                        )
                        last_sent_angle = angle
                        if args.verbose and responses:
                            print(responses[-1])
                else:
                    missed_frames += 1
                    if should_stop_for_loss(missed_frames, args.lost_target_hold_frames):
                        loss_command = "CENTER" if args.on_loss == "center" else "STOP"
                        responses = send_control_command(
                            serial_client,
                            loss_command,
                            response_timeout=args.serial_response_timeout,
                            idle_timeout=args.serial_idle_timeout,
                            require_response=True,
                        )
                        tracker = None
                        tracked_target = None
                        tracking_active = False
                        state.pending_detections = []
                        if loss_command == "CENTER":
                            last_sent_angle = args.center_angle
                        if args.verbose and responses:
                            print(responses[-1])

            draw_overlay(
                frame,
                tracked_target,
                state.pending_detections,
                last_sent_angle,
                tracking_active,
                missed_frames,
                args.on_loss,
            )
            cv2.imshow(WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            if key == ord("d"):
                state.pending_detections = detect_people(detector, frame, args.yolo_confidence)
                if args.verbose:
                    print(f"Detected {len(state.pending_detections)} person candidates")
            elif key == ord("r"):
                bbox = select_target_bbox(frame)
                if bbox is None:
                    continue

                tracker, tracked_target = start_tracking(frame, bbox)
                state.pending_detections = []
                tracking_active = True
                missed_frames = 0
                if args.verbose and tracked_target is not None:
                    print(f"Selected manual bbox={tracked_target.bbox}")
            elif key == ord("c"):
                responses = send_control_command(
                    serial_client,
                    "CENTER",
                    response_timeout=args.serial_response_timeout,
                    idle_timeout=args.serial_idle_timeout,
                    require_response=True,
                )
                tracker = None
                tracked_target = None
                tracking_active = False
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
                tracker = None
                tracked_target = None
                tracking_active = False
                missed_frames = 0
                state.pending_detections = []
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
