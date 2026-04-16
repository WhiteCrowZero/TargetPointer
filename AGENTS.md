# Repository Guidelines

## Project Structure & Module Organization
`docs/项目概述.md` is the current product and architecture brief; read it before changing scope or hardware assumptions. Keep implementation work under `firmware/`: place application code in `firmware/src/`, shared headers in `firmware/include/`, and board, library, or environment configuration in `firmware/config/`. Use `docs/` for design notes, bring-up logs, protocol notes, and calibration records. Store schematics, mechanical sketches, BOMs, and wiring references in `hardware/`. Put repeatable helper scripts such as flashing, serial logging, or host-side helpers in `scripts/`.

## Build, Test, and Development Commands
The repository uses `PlatformIO` for firmware and `uv` for Python environment management. Preferred examples:

- `uv sync` installs project Python dependencies from `pyproject.toml` and `uv.lock`.
- `uv run pio run --project-dir firmware` builds the firmware.
- `uv run pio run --project-dir firmware -t upload` flashes the board.
- `uv run pio test --project-dir firmware -e native` runs host-side protocol tests.
- `uv run python scripts/pointer_serial_cli.py --port COM5 ping` verifies the serial command path.

If you introduce a different toolchain, document the exact command and required board package in `docs/` and expose it through `scripts/`.

## Coding Style & Naming Conventions
Write firmware in Arduino-style C/C++ with 4-space indentation and UTF-8 source files unless a tool requires ASCII. Use `snake_case` for functions and variables, `PascalCase` for classes and structs, and `ALL_CAPS` for protocol tokens or state constants when needed. Keep modules small and hardware-focused; prefer names like `pointer_protocol.cpp`, `servo_driver.cpp`, and `serial_bridge.cpp`. Add brief comments only where timing, pin mapping, or protocol handling is non-obvious.

## Testing Guidelines
Add tests with each new behavior. Put pure logic tests beside the firmware in `firmware/test/`, and name files after the module under test, for example `test_pointer_protocol.cpp`. For hardware-dependent work, record the setup, board revision, and observed result in `docs/硬件调试记录.md`. Validate at least command parsing, serial responses, and servo angle bounds.

## Commit & Pull Request Guidelines
Use short imperative commit messages such as `Add Blue Pill serial servo firmware` or `Document host vision workflow`. Keep commits focused. Pull requests should summarize the hardware or firmware change, list test evidence, link related issues or design notes, and include photos, logs, or serial output when behavior changes on real hardware.
