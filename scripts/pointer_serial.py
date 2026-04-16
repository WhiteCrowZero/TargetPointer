import time

try:
    import serial
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pyserial. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc


class PointerSerialError(RuntimeError):
    pass


def read_serial_lines(device: serial.Serial, response_timeout: float, idle_timeout: float) -> list[str]:
    if response_timeout < 0:
        raise ValueError("response_timeout must be non-negative")
    if idle_timeout < 0:
        raise ValueError("idle_timeout must be non-negative")

    responses: list[str] = []
    deadline = time.monotonic() + response_timeout
    last_activity: float | None = None

    while time.monotonic() < deadline:
        raw_line = device.readline()
        now = time.monotonic()
        if raw_line:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                responses.append(line)
                last_activity = now
            continue

        if last_activity is not None and now - last_activity >= idle_timeout:
            break

    return responses


def send_serial_command(
    device: serial.Serial,
    command: str,
    response_timeout: float,
    idle_timeout: float,
    require_response: bool = False,
    clear_input: bool = True,
) -> list[str]:
    if clear_input:
        device.reset_input_buffer()

    device.write((command + "\n").encode("ascii"))
    device.flush()
    responses = read_serial_lines(device, response_timeout=response_timeout, idle_timeout=idle_timeout)

    for line in responses:
        if line.startswith("ERR:"):
            raise PointerSerialError(f"{command} -> {line}")

    if require_response and not responses:
        raise PointerSerialError(f"{command} -> no response")

    return responses


class PointerSerialClient:
    def __init__(self, port: str, baud: int, timeout: float) -> None:
        self.device = serial.Serial(port=port, baudrate=baud, timeout=timeout)

    def __enter__(self) -> "PointerSerialClient":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self.device.close()

    def read_startup(self, response_timeout: float, idle_timeout: float) -> list[str]:
        return read_serial_lines(self.device, response_timeout=response_timeout, idle_timeout=idle_timeout)

    def send(
        self,
        command: str,
        response_timeout: float,
        idle_timeout: float,
        require_response: bool = False,
        clear_input: bool = True,
    ) -> list[str]:
        return send_serial_command(
            self.device,
            command,
            response_timeout=response_timeout,
            idle_timeout=idle_timeout,
            require_response=require_response,
            clear_input=clear_input,
        )
