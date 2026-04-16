#!/usr/bin/env python3

import argparse
import sys
import time

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pyserial. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc


def open_serial(port: str, baud: int, timeout: float) -> serial.Serial:
    return serial.Serial(port=port, baudrate=baud, timeout=timeout)


def send_command(device: serial.Serial, command: str, settle_delay: float) -> list[str]:
    device.reset_input_buffer()
    device.write((command + "\n").encode("ascii"))
    device.flush()
    time.sleep(settle_delay)

    responses: list[str] = []
    while device.in_waiting:
        line = device.readline().decode("utf-8", errors="replace").strip()
        if line:
            responses.append(line)
    return responses


def build_command(args: argparse.Namespace) -> str:
    if args.command == "ping":
        return "PING"
    if args.command == "center":
        return "CENTER"
    if args.command == "stop":
        return "STOP"
    if args.command == "status":
        return "STATUS?"
    if args.command == "angle":
        return f"ANGLE:{args.angle}"
    if args.command == "target":
        return f"TARGET:{args.target_name}"
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual serial controller for the VoicePointer firmware.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM5 or /dev/ttyUSB0.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--timeout", type=float, default=0.2, help="Read timeout in seconds.")
    parser.add_argument(
        "--settle-delay",
        type=float,
        default=0.2,
        help="Delay after sending a command before reading responses.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ping")
    subparsers.add_parser("center")
    subparsers.add_parser("stop")
    subparsers.add_parser("status")

    angle_parser = subparsers.add_parser("angle")
    angle_parser.add_argument("angle", type=int, help="Servo angle in degrees.")

    target_parser = subparsers.add_parser("target")
    target_parser.add_argument("target_name", help="Target name for logging.")

    args = parser.parse_args()
    command = build_command(args)

    try:
        with open_serial(args.port, args.baud, args.timeout) as device:
            responses = send_command(device, command, args.settle_delay)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    print(f">>> {command}")
    if responses:
        for line in responses:
            print(line)
    else:
        print("(no response)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
