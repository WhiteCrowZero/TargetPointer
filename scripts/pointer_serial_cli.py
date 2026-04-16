#!/usr/bin/env python3

import argparse
import sys
import time

import serial

from pointer_serial import PointerSerialError, PointerSerialClient


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
    raise ValueError(f"Unsupported command: {args.command}")


def build_expected_responses(args: argparse.Namespace) -> list[str]:
    expected = list(args.expect)
    if args.command == "ping":
        expected.append("PONG")
    elif args.command == "center":
        expected.append("OK:CENTER")
    elif args.command == "stop":
        expected.append("OK:STOP")
    elif args.command == "status":
        expected.append("STATUS:")
    elif args.command == "angle":
        expected.append(f"OK:ANGLE:{args.angle}")
    return expected


def validate_expected_responses(command: str, responses: list[str], expected: list[str]) -> None:
    for token in expected:
        if not any(token in line for line in responses):
            raise PointerSerialError(f"{command} -> missing expected response containing '{token}'")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual serial controller for the TargetPointer firmware.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM5 or /dev/ttyUSB0.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--timeout", type=float, default=0.05, help="Per-read timeout in seconds.")
    parser.add_argument("--response-timeout", type=float, default=0.6, help="Total wait time for command responses.")
    parser.add_argument("--idle-timeout", type=float, default=0.08, help="Stop reading after this idle gap.")
    parser.add_argument("--read-startup", type=float, default=0.0, help="Read boot logs for this many seconds before sending.")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the same command multiple times.")
    parser.add_argument("--interval", type=float, default=0.0, help="Delay between repeated sends.")
    parser.add_argument("--expect", action="append", default=[], help="Require a response line containing this text.")
    parser.add_argument(
        "--allow-no-response",
        action="store_true",
        help="Do not fail when the device returns no response.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ping")
    subparsers.add_parser("center")
    subparsers.add_parser("stop")
    subparsers.add_parser("status")

    angle_parser = subparsers.add_parser("angle")
    angle_parser.add_argument("angle", type=int, help="Servo angle in degrees.")

    args = parser.parse_args()
    command = build_command(args)
    expected_responses = build_expected_responses(args)

    try:
        with PointerSerialClient(args.port, args.baud, args.timeout) as device:
            if args.read_startup > 0:
                startup_lines = device.read_startup(args.read_startup, args.idle_timeout)
                if startup_lines:
                    print(">>> startup")
                    for line in startup_lines:
                        print(line)

            for attempt in range(1, args.repeat + 1):
                responses = device.send(
                    command,
                    response_timeout=args.response_timeout,
                    idle_timeout=args.idle_timeout,
                    require_response=not args.allow_no_response,
                )

                if expected_responses:
                    validate_expected_responses(command, responses, expected_responses)

                prefix = f">>> [{attempt}/{args.repeat}] {command}" if args.repeat > 1 else f">>> {command}"
                print(prefix)
                if responses:
                    for line in responses:
                        print(line)
                else:
                    print("(no response)")

                if attempt < args.repeat and args.interval > 0:
                    time.sleep(args.interval)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1
    except PointerSerialError as exc:
        print(f"Protocol error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
