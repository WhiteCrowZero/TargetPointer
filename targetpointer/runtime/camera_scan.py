from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: opencv-python. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc


WINDOWS_CAMERA_BACKENDS = (
    ("msmf", "CAP_MSMF"),
    ("dshow", "CAP_DSHOW"),
)


@dataclass(frozen=True)
class CameraScanResult:
    index: int
    backend: str
    read_ok: bool

    def to_json(self) -> dict[str, object]:
        return {"index": self.index, "backend": self.backend, "read_ok": self.read_ok}


def resolve_backend_constant(backend_name: str) -> int:
    if backend_name in ("auto", "any"):
        return cv2.CAP_ANY

    attribute_name = dict(WINDOWS_CAMERA_BACKENDS).get(backend_name)
    if attribute_name is None:
        raise ValueError(f"Unsupported camera backend: {backend_name}")

    backend_constant = getattr(cv2, attribute_name, None)
    if backend_constant is None:
        raise ValueError(f"OpenCV build does not provide {attribute_name}")
    return int(backend_constant)


def camera_scan_backend_candidates(backend_name: str) -> list[tuple[int, str]]:
    if backend_name != "auto":
        return [(resolve_backend_constant(backend_name), backend_name)]

    candidates: list[tuple[int, str]] = []
    if sys.platform.startswith("win"):
        for candidate_name, attribute_name in WINDOWS_CAMERA_BACKENDS:
            backend_constant = getattr(cv2, attribute_name, None)
            if backend_constant is not None:
                candidates.append((int(backend_constant), candidate_name))
    candidates.append((int(cv2.CAP_ANY), "any"))
    return candidates


def scan_camera_indices(max_index: int, backend_name: str) -> list[CameraScanResult]:
    if max_index < 0:
        raise ValueError("max_index must be non-negative")

    results: list[CameraScanResult] = []
    candidates = camera_scan_backend_candidates(backend_name)
    for camera_index in range(max_index + 1):
        for backend_constant, candidate_name in candidates:
            capture = None
            try:
                capture = cv2.VideoCapture(camera_index, backend_constant)
                if capture.isOpened():
                    results.append(CameraScanResult(camera_index, candidate_name, True))
                    break
            except Exception:
                continue
            finally:
                if capture is not None:
                    capture.release()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local OpenCV camera indices and print JSON.")
    parser.add_argument("--max-index", type=int, default=4, help="Highest camera index to probe.")
    parser.add_argument("--backend", choices=("auto", "any", "msmf", "dshow"), default="auto", help="OpenCV backend.")
    args = parser.parse_args()

    try:
        results = scan_camera_indices(args.max_index, args.backend)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps([item.to_json() for item in results], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
