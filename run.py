#!/usr/bin/env python
"""Run the K2 Automation backend (which also serves the frontend at /).

Usage:
    python run.py [--port 8000] [--host 127.0.0.1] [--reload]

Set NGROK=true to also expose the local server through an ngrok tunnel
(requires the ngrok CLI installed and authenticated already).
"""
import argparse
import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
BACKEND_DIR = Path(__file__).resolve().parent / "backend"


def start_ngrok(port):
    """Launch `ngrok http <port>` and return its public HTTPS URL, or None on failure."""
    try:
        proc = subprocess.Popen(
            ["ngrok", "http", str(port), "--log=stdout"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("NGROK=true but the `ngrok` CLI was not found on PATH.")
        return None
    atexit.register(proc.terminate)

    for _ in range(20):
        time.sleep(0.5)
        if proc.poll() is not None:
            print("ngrok exited before a tunnel came up — check `ngrok config check`.")
            return None
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, ConnectionError):
            continue
        for t in data.get("tunnels", []):
            if t.get("public_url", "").startswith("https"):
                return t["public_url"]
    print("ngrok started but no tunnel URL appeared within 10s.")
    return None


def main():
    parser = argparse.ArgumentParser(description="Run the K2 Automation server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-restart on code changes")
    parser.add_argument(
        "--debug", action="store_true",
        help="Debug mode: verbose uvicorn logging, auto-reload, and detailed in-browser tracebacks on errors",
    )
    args = parser.parse_args()

    # app.main resolves the frontend dir relative to backend/app/, and
    # store.py resolves jobs/ relative to backend/, so backend/ must be on
    # sys.path with cwd there too.
    sys.path.insert(0, str(BACKEND_DIR))

    if args.debug:
        os.environ["K2_DEBUG"] = "1"

    if os.environ.get("NGROK", "").lower() in ("1", "true", "yes"):
        ngrok_url = start_ngrok(args.port)
        if ngrok_url:
            print(f"ngrok tunnel: {ngrok_url}")

    print(f"K2 Automation starting on http://{args.host}:{args.port}" + (" (debug mode)" if args.debug else ""))
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload or args.debug,
        app_dir=str(BACKEND_DIR),
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
