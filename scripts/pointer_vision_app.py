#!/usr/bin/env python3

import argparse
import sys
import time
from dataclasses import dataclass

try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: opencv-python. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pyserial. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

try:
    from ultralytics import YOLO
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: ultralytics. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc


TARGET_ALIASES = {
    "cup": "cup",
    "杯子": "cup",
    "水杯": "cup",
    "remote": "remote",
    "遥控器": "remote",
    "bottle": "bottle",
    "瓶子": "bottle",
    "cell phone": "cell phone",
    "phone": "cell phone",
    "手机": "cell phone",
}


@dataclass
class Detection:
    label: str
    confidence: float
    center_x: float
    center_y: float
    bbox: tuple[int, int, int, int]


class SerialPointerClient:
    def __init__(self, port: str, baud: int, timeout: float) -> None:
        self.device = serial.Serial(port=port, baudrate=baud, timeout=timeout)

    def close(self) -> None:
        self.device.close()

    def send(self, command: str) -> None:
        self.device.write((command + "\n").encode("ascii"))
        self.device.flush()


def normalize_target_name(raw_name: str) -> str:
    normalized = raw_name.strip().lower()
    if normalized not in TARGET_ALIASES:
        supported = ", ".join(sorted(set(TARGET_ALIASES.values())))
        raise ValueError(f"Unsupported target '{raw_name}'. Supported classes: {supported}")
    return TARGET_ALIASES[normalized]


def parse_camera_source(raw_source: str):
    return int(raw_source) if raw_source.isdigit() else raw_source


def map_center_to_angle(center_x: float, frame_width: int, min_angle: int, center_angle: int, max_angle: int) -> int:
    if frame_width <= 0:
        raise ValueError("frame_width must be positive")

    half_width = frame_width / 2.0
    offset = center_x - half_width
    ratio = max(-1.0, min(1.0, offset / half_width))
    half_span = min(center_angle - min_angle, max_angle - center_angle)
    mapped = center_angle + ratio * half_span
    return max(min_angle, min(max_angle, int(round(mapped))))


def choose_detection(results, target_label: str) -> Detection | None:
    best: Detection | None = None
    for result in results:
        names = result.names
        for box in result.boxes:
            class_id = int(box.cls[0].item())
            label = names[class_id]
            if label != target_label:
                continue

            confidence = float(box.conf[0].item())
            x1, y1, x2, y2 = [int(value) for value in box.xyxy[0].tolist()]
            center_x = (x1 + x2) / 2.0
            center_y = (y1 + y2) / 2.0
            detection = Detection(label, confidence, center_x, center_y, (x1, y1, x2, y2))
            if best is None or detection.confidence > best.confidence:
                best = detection
    return best


def read_target_from_speech() -> str:
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: SpeechRecognition. Run `uv sync` in the repository root, then retry with `uv run python ...`."
        ) from exc

    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening for target name...")
        audio = recognizer.listen(source, phrase_time_limit=3)

    try:
        raw_text = recognizer.recognize_google(audio, language="zh-CN")
    except sr.UnknownValueError as exc:
        raise SystemExit("Speech recognition did not understand the target name.") from exc
    except sr.RequestError as exc:
        raise SystemExit(f"Speech recognition request failed: {exc}") from exc

    print(f"Recognized target: {raw_text}")
    return normalize_target_name(raw_text)


def draw_overlay(frame, target_label: str, detection: Detection | None, sent_angle: int | None) -> None:
    height, width = frame.shape[:2]
    center_x = width // 2
    cv2.line(frame, (center_x, 0), (center_x, height), (255, 255, 0), 1)
    cv2.putText(frame, f"target={target_label}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    if sent_angle is not None:
        cv2.putText(frame, f"angle={sent_angle}", (10, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    if detection is None:
        cv2.putText(frame, "status=target not found", (10, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return

    x1, y1, x2, y2 = detection.bbox
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.circle(frame, (int(detection.center_x), int(detection.center_y)), 4, (0, 255, 0), -1)
    cv2.putText(
        frame,
        f"{detection.label} {detection.confidence:.2f}",
        (x1, max(20, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Vision + serial host app for VoicePointer.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM5.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--camera", default="0", help="Camera index or URL. Default: 0.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path or model name.")
    parser.add_argument("--target", help="Target class name. Example: cup or remote.")
    parser.add_argument(
        "--input-mode",
        choices=("text", "speech"),
        default="text",
        help="How the target name is provided.",
    )
    parser.add_argument("--show", action="store_true", help="Show the annotated video window.")
    parser.add_argument("--confidence", type=float, default=0.25, help="Minimum detection confidence.")
    parser.add_argument("--min-angle", type=int, default=20)
    parser.add_argument("--center-angle", type=int, default=90)
    parser.add_argument("--max-angle", type=int, default=160)
    parser.add_argument(
        "--angle-step-threshold",
        type=int,
        default=2,
        help="Only resend angle when the change is at least this many degrees.",
    )
    args = parser.parse_args()

    if args.input_mode == "speech":
        target_label = read_target_from_speech()
    else:
        raw_target = args.target if args.target else input("Target name: ")
        target_label = normalize_target_name(raw_target)

    try:
        serial_client = SerialPointerClient(args.port, args.baud, timeout=0.1)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    capture = cv2.VideoCapture(parse_camera_source(args.camera))
    if not capture.isOpened():
        serial_client.close()
        print("Failed to open camera source.", file=sys.stderr)
        return 1

    model = YOLO(args.model)
    serial_client.send(f"TARGET:{target_label}")
    serial_client.send("CENTER")

    last_sent_angle: int | None = None
    target_missing = False

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                print("Camera frame read failed.", file=sys.stderr)
                return 1

            results = model.predict(frame, conf=args.confidence, verbose=False)
            detection = choose_detection(results, target_label)

            if detection is not None:
                target_missing = False
                angle = map_center_to_angle(
                    detection.center_x,
                    frame.shape[1],
                    args.min_angle,
                    args.center_angle,
                    args.max_angle,
                )
                if last_sent_angle is None or abs(angle - last_sent_angle) >= args.angle_step_threshold:
                    serial_client.send(f"ANGLE:{angle}")
                    last_sent_angle = angle
            elif not target_missing:
                serial_client.send("STOP")
                target_missing = True

            if args.show:
                draw_overlay(frame, target_label, detection, last_sent_angle)
                cv2.imshow("VoicePointer", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break

            time.sleep(0.01)
    finally:
        try:
            serial_client.send("STOP")
        except serial.SerialException:
            pass
        capture.release()
        serial_client.close()
        if args.show:
            cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
