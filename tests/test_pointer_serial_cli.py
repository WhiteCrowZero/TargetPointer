import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_serial import PointerSerialError
from pointer_serial_cli import send_with_fallback, send_with_recovery


class FakeClient:
    def __init__(self, responses_by_command: dict[str, list[str] | Exception]) -> None:
        self.responses_by_command = responses_by_command
        self.sent_commands: list[str] = []
        self.startup_reads: list[tuple[float, float]] = []
        self.read_startup_lines: list[str] = ["BOOT", "OK:CENTER"]

    def send(
        self,
        command: str,
        response_timeout: float,
        idle_timeout: float,
        require_response: bool = False,
        clear_input: bool = True,
    ) -> list[str]:
        del response_timeout, idle_timeout, require_response, clear_input
        self.sent_commands.append(command)
        outcome = self.responses_by_command[command]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def read_startup(self, response_timeout: float, idle_timeout: float) -> list[str]:
        self.startup_reads.append((response_timeout, idle_timeout))
        return list(self.read_startup_lines)


class PointerSerialCliTests(unittest.TestCase):
    def test_send_with_fallback_retries_status_without_question_mark(self) -> None:
        client = FakeClient(
            {
                "STATUS?": PointerSerialError("STATUS? -> ERR:BAD_CMD"),
                "STATUS": ["STATUS:ANGLE=90,LAST=CENTER,RESULT=OK:CENTER"],
            }
        )

        command, responses = send_with_fallback(
            client,
            ["STATUS?", "STATUS"],
            response_timeout=0.1,
            idle_timeout=0.05,
            require_response=True,
        )

        self.assertEqual(command, "STATUS")
        self.assertEqual(responses, ["STATUS:ANGLE=90,LAST=CENTER,RESULT=OK:CENTER"])
        self.assertEqual(client.sent_commands, ["STATUS?", "STATUS"])

    def test_send_with_fallback_does_not_retry_non_bad_cmd_errors(self) -> None:
        client = FakeClient({"STATUS?": PointerSerialError("STATUS? -> no response")})

        with self.assertRaises(PointerSerialError):
            send_with_fallback(
                client,
                ["STATUS?", "STATUS"],
                response_timeout=0.1,
                idle_timeout=0.05,
                require_response=True,
            )

        self.assertEqual(client.sent_commands, ["STATUS?"])

    def test_send_with_recovery_retries_after_no_response(self) -> None:
        client = FakeClient(
            {
                "PING": PointerSerialError("PING -> no response"),
            }
        )
        outcomes = [
            PointerSerialError("PING -> no response"),
            ["PONG"],
        ]

        def ping_outcome(command, response_timeout, idle_timeout, require_response=False, clear_input=True):
            del response_timeout, idle_timeout, require_response, clear_input
            client.sent_commands.append(command)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        client.send = ping_outcome

        command, responses, recovery_lines = send_with_recovery(
            client,
            ["PING"],
            response_timeout=0.1,
            idle_timeout=0.05,
            require_response=True,
            recovery_timeout=1.5,
        )

        self.assertEqual(command, "PING")
        self.assertEqual(responses, ["PONG"])
        self.assertEqual(recovery_lines, ["BOOT", "OK:CENTER"])
        self.assertEqual(client.sent_commands, ["PING", "PING"])
        self.assertEqual(client.startup_reads, [(1.5, 0.05)])


if __name__ == "__main__":
    unittest.main()
