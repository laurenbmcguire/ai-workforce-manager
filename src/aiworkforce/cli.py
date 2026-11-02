"""Command line: `python -m aiworkforce run` and `python -m aiworkforce pm`."""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from .ai_pm import run_pm
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiworkforce", description="AI Workforce Manager")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="generate data if missing, simulate, write charts and reports")
    p_run.add_argument("--root", default=".", help="repository root (default: current directory)")
    p_pm = sub.add_parser("pm", help="run the AI project manager (writes project/STATUS.md)")
    p_pm.add_argument("--root", default=".")
    p_pm.add_argument("--today", default=None, help="override date (YYYY-MM-DD)")
    p_pm.add_argument("--no-llm", action="store_true", help="skip the optional LLM summary")
    args = parser.parse_args(argv)
    if args.command == "run":
        run(args.root)
    elif args.command == "pm":
        today = dt.date.fromisoformat(args.today) if args.today else None
        run_pm(args.root, today=today, use_llm=not args.no_llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
