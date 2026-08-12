#!/usr/bin/env python
"""Run the K2 Automation backend (which also serves the frontend at /).

Usage:
    python run.py [--port 8000] [--host 127.0.0.1] [--reload]
"""
import argparse
import os
import sys
from pathlib import Path

import uvicorn
BACKEND_DIR = Path(__file__).resolve().parent / "backend"


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
