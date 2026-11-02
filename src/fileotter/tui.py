"""Fileotter's interactive terminal UI, in the style of Claude Code.

- Streams the agent's reply as Markdown, with a live spinner while it thinks or runs tools.
- Shows each tool call (``⏺ Convert(report.pdf → docx)``) and a short summary of its result.
- Asks before risky built-ins (shell, write, edit, code execution), with "don't ask again".
- Prompt with history, path tab-completion, ``@path`` mentions, and slash commands.
- Ctrl-C interrupts the current turn; Ctrl-C twice (or Ctrl-D) at an empty prompt exits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .agent import GATED_TOOLS, build_agent, fileotter_home, resolve_model
from .converters import supported_conversions

ACCENT = "#e8956b"  # otter orange
DIM = "grey50"  # rich color name: use only in rich output
PT_DIM = "#808080"  # same grey for prompt_toolkit, which only accepts hex/ANSI names
RESULT_PREVIEW_LINES = 3

COMMANDS: dict[str, str] = {
    "/help": "Show commands and shortcuts",
    "/formats": "List every supported conversion",
    "/clear": "Start a fresh conversation",
    "/model": "Show the model, or switch: /model anthropic/claude-sonnet-5",
    "/session": "Show the session id (resume later with --session <id>)",
    "/exit": "Quit Fileotter",
}

# Friendly names and one-line argument summaries for tool-call lines.
_TOOL_LABELS = {
    "list_files": "List",
    "inspect_file": "Inspect",
    "list_supported_conversions": "Formats",
    "convert_file": "Convert",
    "compress_files": "Archive",
    "extract_archive": "Extract",
    "compress_image": "CompressImage",
    "compress_pdf": "CompressPDF",
    "compress_xlsx": "CompressXLSX",
    "shell": "Shell",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "programmatic_tool_caller": "RunCode",
    "subagent": "Task",
    "todo_write": "Todos",
}


_PATH_ARGS = ("path", "pdf_path", "xlsx_path", "image_path", "archive_path", "file_path", "directory")


def _short_path(value: Any) -> str:
    text = str(value)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home) else text


def _fit(text: str, width: int) -> str:
    """Middle-truncate long strings: ``/private/tmp/…/scratchpad``."""
    if len(text) <= width:
        return text
    keep = (width - 1) // 2
    return f"{text[:keep]}…{text[-keep:]}"


def describe_tool_call(name: str, args: dict[str, Any]) -> str:
    """Render a tool call as ``Label(summary)``, e.g. ``Convert(~/a.pdf → docx)``."""
    label = _TOOL_LABELS.get(name, name)
    if name == "convert_file":
        summary = f"{_short_path(args.get('source_path', '?'))} → {args.get('target_format', '?')}"
    elif name == "compress_files":
        paths = args.get("paths") or []
        summary = f"{', '.join(_short_path(p) for p in paths[:3])}{' …' if len(paths) > 3 else ''}"
        summary += f" → {args.get('archive_format', 'zip')}"
    elif name == "list_files":
        summary = _short_path(args.get("directory", "."))
        if args.get("pattern", "*") != "*":
            summary += f" {args['pattern']}"
    elif name == "shell":
        summary = str(args.get("command", ""))
    else:
        first = next((args[k] for k in _PATH_ARGS if k in args), None)
        if first is not None:
            summary = _short_path(first)
        elif args:
            summary = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
        else:
            summary = ""
    summary = " ".join(summary.split())
    if len(summary) > 100:
        summary = summary[:99] + "…"
    return f"{label}({summary})"


def summarize_inspection(text: str) -> str:
    """One readable line for an ``inspect_file`` result: ``pdf · 734 KB · 3 pages · no text layer``."""
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(info, dict):
        return text
    size = float(info.get("size_bytes", 0))
    unit = "B"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            break
        size /= 1024
    parts = [str(info.get("detected_format", "?")), f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"]
    details = info.get("details") or {}
    for key, word in (("pages", "page"), ("sheets", "sheet"), ("rows", "row"), ("paragraphs", "paragraph")):
        value = details.get(key)
        if isinstance(value, list):
            value = len(value)
        if isinstance(value, int):
            parts.append(f"{value} {word}{'' if value == 1 else 's'}")
    if "width" in details and "height" in details:
        parts.append(f"{details['width']}×{details['height']}")
    if details.get("has_text_layer") is False:
        parts.append("no text layer (scanned?)")
    if details.get("encrypted"):
        parts.append("encrypted")
    line = " · ".join(parts)
    if info.get("warning"):
        line += f"\n{info['warning']}"
    return line


def _tool_result_text(block: dict[str, Any]) -> str:
    parts = []
    for item in block.get("content", []):
        if "text" in item:
            parts.append(item["text"])
        elif "json" in item:
            parts.append(json.dumps(item["json"], indent=2))
    return "\n".join(parts).strip()


class PathAndCommandCompleter(Completer):
    """Completes ``/commands`` at the start of the line and file paths (incl. ``@path``) anywhere."""

    def __init__(self) -> None:
        self._paths = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for cmd, help_text in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=help_text)
            return
        word = re.split(r"\s", text)[-1].removeprefix("@")
        if not word or not (word[0] in "~./" or "/" in word):
            return
        yield from self._paths.get_completions(Document(word, len(word)), complete_event)


class TurnView:
    """Live view of one agent turn: streaming Markdown plus a status spinner."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.started = time.monotonic()
        self.status = "Thinking"
        self.text = ""
        self.live: Live | None = None

    def __rich__(self) -> Group:
        parts: list[Any] = []
        if self.text.strip():
            parts.append(self._bulleted(Markdown(self.text)))
        elapsed = int(time.monotonic() - self.started)
        label = Text.assemble((f"{self.status}… ", ACCENT), (f"({elapsed}s · ctrl+c to interrupt)", DIM))
        parts.append(Spinner("dots", text=label, style=ACCENT))
        return Group(*parts)

    @staticmethod
    def _bulleted(body: Any, bullet: str = "⏺", style: str = "white") -> Table:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=1, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text(bullet, style=style), body)
        return grid

    def start(self) -> None:
        if self.live is None:
            self.live = Live(self, console=self.console, refresh_per_second=12, transient=True)
            self.live.start()

    def stop(self) -> None:
        """Stop the live region and print any streamed text permanently."""
        if self.live is not None:
            self.live.stop()
            self.live = None
        if self.text.strip():
            self.console.print(self._bulleted(Markdown(self.text)))
            self.console.print()
        self.text = ""

    def set_status(self, status: str) -> None:
        self.status = status

    def add_text(self, chunk: str) -> None:
        self.text += chunk

    def print_tool_call(self, name: str, args: dict[str, Any]) -> None:
        line = Text(describe_tool_call(name, args), style="bold")
        self.console.print(self._bulleted(line, style="green"))

    def print_tool_result(self, text: str, error: bool) -> None:
        lines = text.splitlines() or ["(no output)"]
        shown = "\n".join(lines[:RESULT_PREVIEW_LINES])
        if len(lines) > RESULT_PREVIEW_LINES:
            shown += f"\n… +{len(lines) - RESULT_PREVIEW_LINES} lines"
        style = "red" if error or text.startswith("Error:") else DIM
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=4, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text("  ⎿", style=DIM), Text(shown, style=style))
        self.console.print(grid)
        self.console.print()


class FileotterTUI:
    def __init__(self, model: str | None = None, session_id: str | None = None) -> None:
        self.console = Console(highlight=False)
        self.model = resolve_model(model)
        self.session_id = session_id or f"fo-{uuid.uuid4().hex[:8]}"
        self.view: TurnView | None = None
        self._deny_requested = False
        home = fileotter_home()
        home.mkdir(parents=True, exist_ok=True)
        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(home / "history")),
            completer=PathAndCommandCompleter(),
            complete_while_typing=False,
            key_bindings=self._key_bindings(),
            bottom_toolbar=self._toolbar,
            multiline=False,
        )
        self.agent = self._make_agent()

    # ----- setup -------------------------------------------------------------------------------

    def _make_agent(self):
        from strands.vended_interventions.hitl import HumanInTheLoop

        approvals = HumanInTheLoop(enable_trust=True, ask=self._ask_permission)
        return build_agent(
            model=self.model,
            session_id=self.session_id,
            interventions=approvals,
            callback_handler=None,  # the TUI renders the stream itself
        )

    @staticmethod
    def _key_bindings() -> KeyBindings:
        kb = KeyBindings()

        @kb.add("escape", "enter")  # Option/Alt+Enter
        @kb.add("c-j")  # Ctrl+J
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        return kb

    def _toolbar(self) -> HTML:
        model = self.model or "bedrock (default)"
        return HTML(
            f"  <b>/</b> commands · <b>tab</b> paths · <b>⌥⏎</b> newline · <b>ctrl+d</b> exit"
            f"      <style fg='{PT_DIM}'>{model} · {self.session_id}</style>"
        )

    def _banner(self) -> None:
        body = Text.assemble(
            ("✻ ", ACCENT),
            ("Welcome to Fileotter", "bold"),
            ("\n\n  Convert, compress, and inspect any file on this machine.\n", ""),
            ("  Files never leave your computer.\n\n", DIM),
            ("  cwd:     ", DIM),
            (_fit(_short_path(Path.cwd()), 60), ""),
            ("\n  model:   ", DIM),
            (self.model or "Amazon Bedrock (harness default)", ""),
            ("\n  session: ", DIM),
            (self.session_id, ""),
        )
        self.console.print(Panel(body, border_style=ACCENT, expand=False, padding=(0, 2)))
        self.console.print(
            Text.assemble(
                ("  Try: ", DIM),
                ('"compress this file"', ""),
                (" · ", DIM),
                ('"turn the pdf in Downloads into a word doc and zip it"', ""),
                (" · ", DIM),
                ("drag a file in", ""),
                (" · ", DIM),
                ("/help", ""),
            )
        )
        self.console.print()

    # ----- permission prompts ------------------------------------------------------------------

    async def _ask_permission(self, prompt: str, **_: Any) -> str:
        match = re.match(r'Approve "([^"]+)"', prompt)
        tool = match.group(1) if match else "tool"
        if tool not in GATED_TOOLS:
            return "y"  # Fileotter's own tools and read-only built-ins run without asking

        args: dict[str, Any] = {}
        if "Input: " in prompt:
            try:
                args = json.loads(prompt.split("Input: ", 1)[1])
            except json.JSONDecodeError:
                pass

        if self.view:
            self.view.stop()
        title = {"shell": "Shell command", "write": "Write file", "edit": "Edit file"}.get(tool, "Run code")
        detail = (
            args.get("command")
            or args.get("path")
            or args.get("file_path")
            or args.get("code")
            or json.dumps(args, indent=2)[:800]
        )
        body = Text.assemble(
            (f"{title}\n\n", "bold"),
            (f"  {detail}\n\n", ""),
            ("Do you want to proceed?\n", ""),
            ("  1. Yes\n", ""),
            (f"  2. Yes, and don't ask again for {_TOOL_LABELS.get(tool, tool)} this session\n", ""),
            ("  3. No, and tell Fileotter what to do differently", ""),
        )
        self.console.print(Panel(body, border_style="yellow", expand=False, padding=(0, 1)))
        while True:
            try:
                choice = (await PromptSession().prompt_async("  ❯ ")).strip().lower()
            except (KeyboardInterrupt, EOFError):
                choice = "3"
            if choice in ("1", "y", "yes", ""):
                answer = "y"
                break
            if choice in ("2", "a", "always"):
                answer = "t"
                break
            if choice in ("3", "n", "no"):
                answer = "n"
                self._deny_requested = True
                self.agent.cancel(after_current_tools=True)
                break
            self.console.print("  [grey50]Type 1, 2, or 3[/]")
        self.console.print()
        if self.view:
            self.view.set_status("Running" if answer != "n" else "Stopping")
            self.view.start()
        return answer

    # ----- a single turn -----------------------------------------------------------------------

    async def run_turn(self, prompt: str) -> None:
        prompt = re.sub(r"(?<!\S)@(?=[~./])", "", prompt)  # "@~/file.pdf" mention -> plain path
        view = self.view = TurnView(self.console)
        self._deny_requested = False
        loop = asyncio.get_running_loop()
        interrupted = False

        def on_sigint() -> None:
            nonlocal interrupted
            interrupted = True
            view.set_status("Interrupting")
            self.agent.cancel()

        loop.add_signal_handler(signal.SIGINT, on_sigint)
        pending: dict[str, tuple[str, dict[str, Any]]] = {}
        result = None
        view.start()
        try:
            async for event in self.agent.stream_async(prompt):
                if "data" in event:
                    view.add_text(event["data"])
                    view.set_status("Writing")
                elif event.get("reasoning"):
                    view.set_status("Thinking")
                elif "current_tool_use" in event:
                    name = (event["current_tool_use"] or {}).get("name")
                    if name:
                        view.set_status(f"Preparing {_TOOL_LABELS.get(name, name)}")
                elif "message" in event:
                    self._render_message(event["message"], view, pending)
                elif "result" in event:
                    result = event["result"]
        except Exception as exc:  # model/provider errors: show them, keep the session alive
            view.stop()
            self._print_error(exc)
            return
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            view.stop()
            self.view = None

        elapsed = time.monotonic() - view.started
        if interrupted:
            self.console.print("  [yellow]⎿  Interrupted.[/] [grey50]What should Fileotter do instead?[/]\n")
        elif self._deny_requested:
            self.console.print("  [yellow]⎿  Stopped.[/] [grey50]Tell Fileotter what to do differently.[/]\n")
        self._print_footer(result, elapsed)

    def _render_message(
        self, message: dict[str, Any], view: TurnView, pending: dict[str, tuple[str, dict[str, Any]]]
    ) -> None:
        content = message.get("content", [])
        if message.get("role") == "assistant":
            tool_uses = [b["toolUse"] for b in content if "toolUse" in b]
            if not tool_uses:
                return
            view.stop()  # freeze any text written before the tool calls
            for use in tool_uses:
                args = use.get("input") or {}
                pending[use["toolUseId"]] = (use["name"], args)
                view.print_tool_call(use["name"], args)
            names = {_TOOL_LABELS.get(u["name"], u["name"]) for u in tool_uses}
            view.set_status(f"Running {', '.join(sorted(names))}")
            view.start()
        elif message.get("role") == "user":
            results = [b["toolResult"] for b in content if "toolResult" in b]
            if not results:
                return
            view.stop()
            for res in results:
                text = _tool_result_text(res)
                if pending.get(res.get("toolUseId", ""), ("",))[0] == "inspect_file":
                    text = summarize_inspection(text)
                view.print_tool_result(text, res.get("status") == "error")
            view.set_status("Thinking")
            view.start()

    def _print_footer(self, result: Any, elapsed: float) -> None:
        tokens = None
        try:
            tokens = result.metrics.accumulated_usage["totalTokens"]
        except (AttributeError, KeyError, TypeError):
            pass
        stats = f"Worked for {elapsed:.0f}s" + (f" · {tokens / 1000:.1f}k tokens" if tokens else "")
        self.console.print(Text.assemble(("✻ ", ACCENT), (stats, DIM)))
        self.console.print()

    def _print_error(self, exc: Exception) -> None:
        message = str(exc) or type(exc).__name__
        self.console.print(Text.assemble(("  ⎿  Error: ", "bold red"), (message, "red")))
        lowered = (type(exc).__name__ + message).lower()
        if any(k in lowered for k in ("credential", "api_key", "apikey", "auth", "token", "unrecognizedclient")):
            self.console.print(
                "  [grey50]Model credentials look missing or invalid. For Anthropic:\n"
                "    uv sync --extra anthropic && export ANTHROPIC_API_KEY=... "
                "FILEOTTER_MODEL=anthropic/claude-sonnet-5\n"
                "  For Bedrock: set AWS_BEARER_TOKEN_BEDROCK (or run aws configure) and AWS_REGION.[/]"
            )
        self.console.print()

    # ----- slash commands ----------------------------------------------------------------------

    def _handle_command(self, line: str) -> bool:
        """Run a slash command. Returns False when the app should exit."""
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        if cmd in ("/exit", "/quit"):
            return False
        if cmd == "/help":
            table = Table.grid(padding=(0, 3))
            for name, help_text in COMMANDS.items():
                table.add_row(Text(name, style=ACCENT), help_text)
            self.console.print(table)
            self.console.print(
                "\n[grey50]Shortcuts: tab completes paths · @path mentions a file · ⌥⏎ or ctrl+j for a newline · "
                "ctrl+c interrupts a turn · ctrl+d exits.\n"
                "You can drag a file from Finder into the terminal to paste its path.[/]\n"
            )
        elif cmd == "/formats":
            table = Table(box=None, padding=(0, 2), show_header=True, header_style="bold")
            table.add_column("from", style=ACCENT)
            table.add_column("to")
            for src, targets in supported_conversions().items():
                table.add_row(src, ", ".join(targets))
            self.console.print(table)
            self.console.print()
        elif cmd == "/clear":
            self.session_id = f"fo-{uuid.uuid4().hex[:8]}"
            self.agent = self._make_agent()
            self.console.clear()
            self._banner()
        elif cmd == "/model":
            if not arg:
                self.console.print(f"  model: {self.model or 'Amazon Bedrock (harness default)'}\n")
            else:
                previous = self.model
                self.model = arg
                try:
                    self.agent = self._make_agent()  # same session id: the conversation carries over
                except Exception as exc:
                    self.model = previous
                    self._print_error(exc)
                else:
                    self.console.print(f"  [green]⎿  Switched to {arg}[/]\n")
        elif cmd == "/session":
            self.console.print(f"  session: {self.session_id}  [grey50](fileotter --session {self.session_id})[/]\n")
        else:
            self.console.print(f"  [red]Unknown command {cmd}.[/] [grey50]Type /help.[/]\n")
        return True

    # ----- main loop ---------------------------------------------------------------------------

    async def run(self, initial: str | None = None) -> None:
        self._banner()
        if initial:
            self._echo_user(initial)
            await self.run_turn(initial)
        armed_exit = False
        while True:
            try:
                line = await self.session.prompt_async(HTML(f"<style fg='{ACCENT}'>❯</style> "))
            except KeyboardInterrupt:
                if armed_exit:
                    break
                armed_exit = True
                self.console.print("[grey50]  Press ctrl+c again to exit[/]")
                continue
            except EOFError:
                break
            armed_exit = False
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self._handle_command(line):
                    break
                continue
            self.console.print()
            await self.run_turn(line)
        self.console.print(f"[grey50]Bye! Resume this conversation with: fileotter --session {self.session_id}[/]")

    def _echo_user(self, text: str) -> None:
        self.console.print(Text.assemble(("❯ ", ACCENT), (text, "")))
        self.console.print()

    async def run_once(self, prompt: str) -> None:
        """Non-interactive: run one request with the same rendering, then exit."""
        self._echo_user(prompt)
        await self.run_turn(prompt)


def _route_logs_to_file() -> None:
    """Library warnings would garble the UI; keep them in ~/.fileotter/fileotter.log instead."""
    home = fileotter_home()
    home.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(home / "fileotter.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)


def run_tui(model: str | None, session_id: str | None, initial: str | None, once: bool) -> None:
    _route_logs_to_file()
    app = FileotterTUI(model=model, session_id=session_id)
    try:
        asyncio.run(app.run_once(initial) if once and initial else app.run(initial))
    except KeyboardInterrupt:
        pass
