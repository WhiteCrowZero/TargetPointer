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


def build_command_candidates(args: argparse.Namespace) -> list[str]:
    command = build_command(args)
    if args.command == "status":
        return [command, "STATUS"]
    return [command]


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


def send_with_fallback(
    device: PointerSerialClient,
    commands: list[str],
    response_timeout: float,
    idle_timeout: float,
    require_response: bool,
) -> tuple[str, list[str]]:
    last_error: PointerSerialError | None = None

    for index, command in enumerate(commands):
        try:
            responses = device.send(
                command,
                response_timeout=response_timeout,
                idle_timeout=idle_timeout,
                require_response=require_response,
            )
            return command, responses
        except PointerSerialError as exc:
            last_error = exc
            is_last_candidate = index == len(commands) - 1
            if is_last_candidate or "ERR:BAD_CMD" not in str(exc):
                raise

    raise last_error if last_error is not None else PointerSerialError("No command candidates were provided")


def send_with_recovery(
    device: PointerSerialClient,
    commands: list[str],
    response_timeout: float,
    idle_timeout: float,
    require_response: bool,
    recovery_timeout: float,
) -> tuple[str, list[str], list[str]]:
    try:
        command, responses = send_with_fallback(
            device,
            commands,
            response_timeout=response_timeout,
            idle_timeout=idle_timeout,
            require_response=require_response,
        )
        return command, responses, []
    except PointerSerialError as exc:
        if recovery_timeout <= 0 or "no response" not in str(exc):
            raise

    startup_lines = device.read_startup(recovery_timeout, idle_timeout)
    command, responses = send_with_fallback(
        device,
        commands,
        response_timeout=response_timeout,
        idle_timeout=idle_timeout,
        require_response=require_response,
    )
    return command, responses, startup_lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual serial controller for the TargetPointer firmware.")
    parser.add_argument("--port", required=True, help="Serial port, for example COM5 or /dev/ttyUSB0.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--timeout", type=float, default=0.05, help="Per-read timeout in seconds.")
    parser.add_argument("--response-timeout", type=float, default=0.6, help="Total wait time for command responses.")
    parser.add_argument("--idle-timeout", type=float, default=0.08, help="Stop reading after this idle gap.")
    parser.add_argument("--read-startup", type=float, default=0.0, help="Read boot logs for this many seconds before sending.")
    parser.add_argument(
        "--recovery-timeout",
        type=float,
        default=1.5,
        help="If a command gets no response, wait this long for startup logs and retry once.",
    )
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
    primary_command = build_command(args)
    command_candidates = build_command_candidates(args)
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
                used_command, responses, recovery_lines = send_with_recovery(
                    device,
                    command_candidates,
                    response_timeout=args.response_timeout,
                    idle_timeout=args.idle_timeout,
                    require_response=not args.allow_no_response,
                    recovery_timeout=args.recovery_timeout,
                )

                if expected_responses:
                    validate_expected_responses(used_command, responses, expected_responses)

                command_label = primary_command if used_command == primary_command else f"{primary_command} (fallback {used_command})"
                prefix = f">>> [{attempt}/{args.repeat}] {command_label}" if args.repeat > 1 else f">>> {command_label}"
                if recovery_lines:
                    print(">>> recovery")
                    for line in recovery_lines:
                        print(line)
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
