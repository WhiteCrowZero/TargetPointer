import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_serial import PointerSerialError
from pointer_serial_cli import send_with_fallback


class FakeClient:
    def __init__(self, responses_by_command: dict[str, list[str] | Exception]) -> None:
        self.responses_by_command = responses_by_command
        self.sent_commands: list[str] = []

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


if __name__ == "__main__":
    unittest.main()
