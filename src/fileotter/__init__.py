"""Fileotter: convert and compress any file on your system, powered by the Strands harness."""

from __future__ import annotations

import argparse

__all__ = ["main"]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fileotter",
        description="Interactive AI assistant that converts, compresses, and inspects files locally.",
    )
    parser.add_argument("request", nargs="*", help='optional first message, e.g. "convert ~/a.pdf to word"')
    parser.add_argument("-p", "--print", action="store_true", help="run the request once and exit (no chat)")
    parser.add_argument("--model", help="Strands provider/model, e.g. anthropic/claude-sonnet-5")
    parser.add_argument("--session", help="session id to resume a previous conversation")
    parser.add_argument("--formats", action="store_true", help="list supported conversions and exit")
    args = parser.parse_args()
    _load_env()

    if args.formats:
        from .converters import supported_conversions

        for src, targets in supported_conversions().items():
            print(f"{src:>5} -> {', '.join(targets)}")
        return

    request = " ".join(args.request) or None
    if args.print and not request:
        parser.error("--print needs a request")

    from .tui import run_tui

    run_tui(model=args.model, session_id=args.session, initial=request, once=args.print)


def _load_env() -> None:
    """Load API keys from ``.env`` files without overriding variables already set in the shell.

    Looks in the current folder (and its parents), then ``~/.fileotter/.env`` so keys also work
    when Fileotter is run from any folder.
    """
    from dotenv import find_dotenv, load_dotenv

    from .agent import fileotter_home

    load_dotenv(find_dotenv(usecwd=True), override=False)
    load_dotenv(fileotter_home() / ".env", override=False)
