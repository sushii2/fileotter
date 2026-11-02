"""Builds the Fileotter agent on top of the Strands harness."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from strands import Agent
from strands_harness import create_harness

from .tools import ALL_TOOLS

INSTRUCTIONS = """\
You are Fileotter, a friendly assistant that converts, compresses, and inspects files on the
user's own computer. People talk to you casually ("compress this", "make it a word doc and zip
it", "shrink the photos for email", "what is this file?"). Your job is to understand what they
mean and get it done, not to make them type exact commands.

Understanding the request
- The user is working in {cwd}. Relative paths and phrases like "here" refer to that folder.
- Resolve references yourself. "this file", "it", "that one", "the pdf", "the photo I just
  downloaded" mean, in order of preference: a file named in this message; the file you most
  recently worked on or produced in this conversation; otherwise look with list_files (in the
  working directory, or ~/Downloads and ~/Desktop when they say "downloaded" / "on my desktop").
  If exactly one file fits, use it and say which one you picked. If several fit, ask one short
  question with a numbered list of the candidates (newest first) and wait for the answer.
- Never run shell commands just to look around: list_files and inspect_file are read-only and
  need no permission. Use the shell only when the user asks for something the Fileotter tools
  can't do.
- If the user sends just a path (for example a file dragged into the terminal) with no request,
  inspect it, tell them in one sentence what it is, and suggest the two or three most useful
  things you could do with it.
- Infer sensible settings from intent: "for email" means small (PDF quality ~40, images about
  1600px); "for print" means keep quality high. Don't quiz the user about parameters they didn't
  mention.

Doing the work
- Do conversions and compression ONLY with the Fileotter tools. Never upload user files to a
  website or third-party service.
- Multi-step requests ("convert and compress", "turn these into a PDF and zip them") run in one
  go: feed each step's output into the next step. Tell the user about every file you created.
- Pick the right compressor: compress_pdf for PDFs, compress_xlsx for Excel, compress_image for
  photos, compress_files to make a zip/tar archive (for Word documents and other formats, "compress"
  means a zip unless they asked for something else).
- When a file's type or contents are unclear, call inspect_file first. Warn before converting a
  scanned PDF (no text layer); it converts to empty text.
- If a conversion isn't supported directly, check list_supported_conversions for a route through an
  intermediate format, and say what will be lost (e.g. PDF -> Word keeps text, not layout or images).
- Never overwrite or delete a user's file without asking. Outputs go next to the source file unless
  the user names a location. If an output already exists, ask whether to replace it or use a new name.

Talking to the user
- Write like a helpful person, not a log file: a sentence or two on what you did and what came out
  (file name, size, how much smaller), plus anything they lost in the conversion. No raw JSON.
- When it helps, end with one short, specific offer for a next step ("Want me to zip it too?").
- Keep the conversation going: the user's next message usually builds on this one ("now make it
  smaller", "do the same for the other one").
"""

DEFAULT_MODEL_ENV = "FILEOTTER_MODEL"
HOME_ENV = "FILEOTTER_HOME"

# Built-in tools that can change the system or run arbitrary code. The interactive CLI asks the
# user before each call; Fileotter's own tools never overwrite or delete without an explicit flag.
GATED_TOOLS = frozenset({"shell", "write", "edit", "programmatic_tool_caller"})


def fileotter_home() -> Path:
    """Where Fileotter keeps harness state (sessions, memory, skills): ``$FILEOTTER_HOME`` or ``~/.fileotter``.

    Fileotter is run from arbitrary folders (Downloads, Desktop, ...), so state must not land in the cwd.
    """
    return Path(os.environ.get(HOME_ENV, "~/.fileotter")).expanduser()


DEFAULT_ANTHROPIC_MODEL = "anthropic/claude-sonnet-5-5"


def resolve_model(model: str | None = None) -> str | None:
    """Pick the model: explicit arg, then ``$FILEOTTER_MODEL``, then Anthropic if ``ANTHROPIC_API_KEY``
    is set, else None for the harness default (Amazon Bedrock)."""
    if model or os.environ.get(DEFAULT_MODEL_ENV):
        return model or os.environ[DEFAULT_MODEL_ENV]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return DEFAULT_ANTHROPIC_MODEL
    return None


def build_agent(model: str | None = None, session_id: str | None = None, **overrides: Any) -> Agent:
    """Create the Fileotter agent.

    ``model`` takes a Strands ``provider/name`` string (e.g. ``anthropic/claude-sonnet-5``);
    it falls back to ``$FILEOTTER_MODEL``, then the harness default (Amazon Bedrock).
    ``session_id`` persists the conversation so it can be resumed later with the same id.
    Any other keyword is forwarded to ``create_harness`` (e.g. ``interventions``, ``callback_handler``).
    """
    home = fileotter_home()
    (home / "skills").mkdir(parents=True, exist_ok=True)  # drop Agent Skills here; harness warns if missing
    session: dict[str, Any] = {"dir": str(home / "sessions")}
    if session_id:
        session["id"] = session_id
    options: dict[str, Any] = {
        "instructions": INSTRUCTIONS.format(cwd=Path.cwd()),
        "tools": ALL_TOOLS,
        # Fileotter is local-first: no web tools, so files and their contents stay on this machine.
        "builtin_tools": ["shell", "read", "write", "edit", "programmatic_tool_caller", "subagent"],
        "session": session,
        "memory": {"dir": str(home / "memory")},
        "skills": str(home / "skills"),
    }
    resolved_model = resolve_model(model)
    if resolved_model:
        options["model"] = resolved_model
    options.update(overrides)
    return create_harness(**options)
