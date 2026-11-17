"""Start the local Areca Cutter server and open its browser workspace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent


def is_ready(url: str) -> bool:
    try:
        with urlopen(url + "/api/health", timeout=1) as response:
            health = json.load(response)
            return health.get("application") == "areca-cutter" and Path(health.get("data_root", "")).resolve() == ROOT
    except (OSError, URLError, ValueError):
        return False


def select_port(preferred: int) -> int:
    for port in range(preferred, min(preferred + 20, 65536)):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found. Close an old server and try again.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Areca Cutter")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    url = f"http://127.0.0.1:{args.port}"
    if is_ready(url):
        print(f"Areca Cutter is already running: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    port = select_port(args.port)
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
    )
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if server.poll() is not None:
                print("Application could not start. Run setup.bat and review the error above.", flush=True)
                return server.returncode or 1
            if is_ready(url):
                print(f"Areca Cutter is ready: {url}\nKeep this window open. Press Ctrl+C to stop.", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return server.wait()
            time.sleep(0.25)
        print("Application startup timed out. Review the error above.", flush=True)
        return 1
    except KeyboardInterrupt:
        print("Stopping Areca Cutter...", flush=True)
        return 0
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
