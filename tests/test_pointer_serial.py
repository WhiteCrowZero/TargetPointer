import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_serial import PointerSerialError, read_serial_lines, send_serial_command


class FakeSerial:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.writes: list[bytes] = []
        self.input_reset_count = 0
        self.flush_count = 0

    def reset_input_buffer(self) -> None:
        self.input_reset_count += 1

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        self.flush_count += 1

    def readline(self) -> bytes:
        if self.responses:
            return self.responses.pop(0)
        return b""


class PointerSerialTests(unittest.TestCase):
    def test_read_serial_lines_collects_non_empty_lines(self) -> None:
        fake = FakeSerial([b"BOOT\n", b"\n", b"OK:CENTER\n"])
        lines = read_serial_lines(fake, response_timeout=0.05, idle_timeout=0.0)
        self.assertEqual(lines, ["BOOT", "OK:CENTER"])

    def test_send_serial_command_raises_on_error_response(self) -> None:
        fake = FakeSerial([b"ERR:BAD_CMD\n"])
        with self.assertRaises(PointerSerialError):
            send_serial_command(fake, "PING", response_timeout=0.05, idle_timeout=0.0, require_response=True)

    def test_send_serial_command_requires_response_when_requested(self) -> None:
        fake = FakeSerial([])
        with self.assertRaises(PointerSerialError):
            send_serial_command(fake, "PING", response_timeout=0.0, idle_timeout=0.0, require_response=True)


if __name__ == "__main__":
    unittest.main()
