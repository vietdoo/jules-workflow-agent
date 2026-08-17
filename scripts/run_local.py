#!/usr/bin/env python3
"""Run FastAPI, Next.js, and Telegram together for the local harness."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

from dotenv import load_dotenv


ROOT: Final = Path(__file__).resolve().parents[1]


def _request_shutdown(_: int, __: object) -> None:
    """Turn process-manager termination signals into a graceful runner exit."""

    raise KeyboardInterrupt


def _command(label: str, args: list[str]) -> tuple[str, list[str]]:
    """Return a labelled command for a child local-harness service."""

    return label, args


def main() -> int:
    """Start all local interfaces and stop every child when one exits."""

    previous_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
    previous_sighup = signal.signal(signal.SIGHUP, _request_shutdown)
    load_dotenv(ROOT / ".env")
    environment = os.environ.copy()
    environment.setdefault("PYTHONUNBUFFERED", "1")
    web_port = environment.get("WEB_UI_PORT", "3000")

    commands = [
        _command("api", [sys.executable, "-m", "apps.api"]),
        _command("web", ["pnpm", "--dir", "apps/web", "exec", "next", "dev", "-p", web_port]),
        _command("telegram", [sys.executable, "-m", "src.main"]),
    ]
    children: list[tuple[str, subprocess.Popen[str]]] = []

    try:
        for label, command in commands:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                text=True,
                start_new_session=True,
            )
            children.append((label, process))
            print(f"[harness] started {label} (pid={process.pid})", flush=True)

        while True:
            for label, process in children:
                exit_code = process.poll()
                if exit_code is not None:
                    print(
                        f"[harness] {label} exited with code {exit_code}; stopping all services.",
                        flush=True,
                    )
                    return exit_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[harness] shutdown requested.", flush=True)
        return 0
    finally:
        for _, process in children:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    continue
        deadline = time.monotonic() + 8
        for _, process in children:
            if process.poll() is None:
                remaining = max(deadline - time.monotonic(), 0.1)
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        continue
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGHUP, previous_sighup)


if __name__ == "__main__":
    raise SystemExit(main())
