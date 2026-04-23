# Repository Guidelines

## Environment Authority
The project runtime and active development environment are now Windows-native. Treat the Windows workspace as the source of truth for device access, flashing, serial debugging, camera access, and day-to-day execution. The WSL copy is only a code mirror for reading, patching, or lightweight checks when convenient; do not assume WSL has working hardware mappings.

When giving commands or workflow guidance:

- Prefer Windows-first instructions for `uv`, `PlatformIO`, serial tools, camera tools, and any end-to-end validation.
- Prefer `COMx` examples over `/dev/ttyUSB*` unless the user explicitly asks for WSL or Linux handling.
- Call out when a result was only checked in the WSL mirror and still needs Windows-side validation.
- Do not recommend WSL USB/serial/camera passthrough as the default workflow.

## Sync Requirement
After every code or documentation change made in this WSL mirror, sync the updated repository contents to the Windows workspace at `D:\ComputerScience\Python\temp\TargetPointer` before reporting completion. Treat that sync step as part of the normal done criteria, not an optional follow-up.

When working from WSL:

- Mirror changed files to `/mnt/d/ComputerScience/Python/temp/TargetPointer`.
- Sync only normal project files such as source code, headers, scripts, docs, configuration, and other repo-tracked assets needed for development.
- Never sync environment or machine-local artifacts such as `.venv`, `.env`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.pio`, `.git`, downloaded model caches, temporary files, or OS/editor metadata.
- When in doubt, prefer syncing the specific changed files instead of bulk-copying directories.
- If sync could not be completed, say so explicitly in the final response.
- Do not assume the Windows workspace already reflects the latest WSL edits.

## Project Structure & Module Organization
`docs/项目概述.md` is the current product and architecture brief; read it before changing scope or hardware assumptions. Keep implementation work under `firmware/`: place application code in `firmware/src/`, shared headers in `firmware/include/`, and board, library, or environment configuration in `firmware/config/`. Use `docs/` for design notes, bring-up logs, protocol notes, and calibration records. Store schematics, mechanical sketches, BOMs, and wiring references in `hardware/`. Put repeatable helper scripts such as flashing, serial logging, or host-side helpers in `scripts/`.

## Build, Test, and Development Commands
The repository uses `PlatformIO` for firmware and `uv` for Python environment management. Windows is the primary execution environment. Preferred examples:

- `uv sync` installs project Python dependencies from `pyproject.toml` and `uv.lock`.
- `uv run pio run --project-dir firmware` builds the firmware.
- `uv run pio run --project-dir firmware -t upload` flashes the board.
- `uv run pio test --project-dir firmware -e native` runs host-side protocol tests.
- `uv run python scripts/pointer_serial_cli.py --port COM4 ping` verifies the serial command path.

If you introduce a different toolchain, document the exact Windows command and required board package in `docs/` and expose it through `scripts/`. If a command is only known to work in WSL, label it explicitly as mirror-only or secondary.

## Coding Style & Naming Conventions
Write firmware in Arduino-style C/C++ with 4-space indentation and UTF-8 source files unless a tool requires ASCII. Use `snake_case` for functions and variables, `PascalCase` for classes and structs, and `ALL_CAPS` for protocol tokens or state constants when needed. Keep modules small and hardware-focused; prefer names like `pointer_protocol.cpp`, `servo_driver.cpp`, and `serial_bridge.cpp`. Add brief comments only where timing, pin mapping, or protocol handling is non-obvious.

## Testing Guidelines
Add tests with each new behavior. Put pure logic tests beside the firmware in `firmware/test/`, and name files after the module under test, for example `test_pointer_protocol.cpp`. For hardware-dependent work, record the setup, board revision, Windows host environment, and observed result in `docs/硬件调试记录.md`. Validate at least command parsing, serial responses, and servo angle bounds. Treat WSL-only verification as incomplete for any feature that depends on serial ports, cameras, GUI windows, upload flows, or other directly attached devices.

## Code Review Reporting
When the user asks for a code review, default to reporting findings with `P0`, `P1`, `P2`, `P3` style priority levels rather than generic severity labels. Unless the user explicitly asks otherwise, focus the main review conclusion on functional issues only, especially algorithm/control problems, error-handling flaws, and safety boundaries that affect correctness or device behavior. Keep non-functional style, readability, or architecture suggestions out of the main findings list unless they directly cause a functional risk.

## Commit & Pull Request Guidelines
Use short imperative commit messages such as `Add Blue Pill serial servo firmware` or `Document host vision workflow`. Keep commits focused. Pull requests should summarize the hardware or firmware change, list test evidence, link related issues or design notes, and include photos, logs, or serial output when behavior changes on real hardware.
