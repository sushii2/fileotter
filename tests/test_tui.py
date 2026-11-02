import asyncio

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from fileotter.tui import FileotterTUI, describe_tool_call


class FakeResult:
    class metrics:  # noqa: N801  (mimics AgentResult.metrics)
        accumulated_usage = {"totalTokens": 2400}


class FakeAgent:
    """Stands in for the Strands agent: replays a scripted event stream."""

    def __init__(self, events):
        self.events = events
        self.cancelled = None

    async def stream_async(self, prompt):
        self.prompt = prompt
        for event in self.events:
            await asyncio.sleep(0)
            yield event

    def cancel(self, **kwargs):
        self.cancelled = kwargs


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FILEOTTER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("FILEOTTER_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tui = FileotterTUI()
    tui.console = Console(record=True, width=120, force_terminal=False)
    return tui


def test_describe_tool_call():
    assert describe_tool_call("convert_file", {"source_path": "/x/a.pdf", "target_format": "docx"}) == (
        "Convert(/x/a.pdf → docx)"
    )
    assert describe_tool_call("shell", {"command": "ls   -la\n"}) == "Shell(ls -la)"
    assert describe_tool_call("compress_pdf", {"pdf_path": "deck.pdf"}) == "CompressPDF(deck.pdf)"
    assert len(describe_tool_call("shell", {"command": "x" * 500})) < 120


def test_state_lives_in_fileotter_home_not_cwd(app, tmp_path):
    assert (tmp_path / "home").is_dir()
    assert not (tmp_path / ".agent").exists()


def test_turn_renders_text_tool_calls_and_results(app):
    app.agent = FakeAgent(
        [
            {"data": "Converting **now**."},
            {"current_tool_use": {"name": "convert_file"}},
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Converting now."},
                        {
                            "toolUse": {
                                "toolUseId": "t1",
                                "name": "convert_file",
                                "input": {"source_path": "report.pdf", "target_format": "docx"},
                            }
                        },
                    ],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": "t1",
                                "status": "success",
                                "content": [{"text": "Converted report.pdf -> report.docx (12 KB)"}],
                            }
                        }
                    ],
                }
            },
            {"data": "Done: `report.docx`."},
            {"result": FakeResult()},
        ]
    )
    asyncio.run(app.run_turn("convert @./report.pdf to word"))
    out = app.console.export_text()

    assert app.agent.prompt == "convert ./report.pdf to word"  # @mention stripped
    assert out.index("Converting now.") < out.index("Convert(report.pdf → docx)") < out.index("Done: report.docx")
    assert "⎿  Converted report.pdf -> report.docx (12 KB)" in out
    assert "2.4k tokens" in out


def test_long_tool_results_are_truncated(app):
    app.agent = FakeAgent(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": [{"toolUse": {"toolUseId": "t", "name": "shell", "input": {}}}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [
                        {"toolResult": {"toolUseId": "t", "content": [{"text": "\n".join(map(str, range(10)))}]}}
                    ],
                }
            },
        ]
    )
    asyncio.run(app.run_turn("go"))
    out = app.console.export_text()
    assert "… +7 lines" in out and "\n9" not in out


def test_model_errors_are_shown_with_credentials_hint(app):
    class Failing(FakeAgent):
        async def stream_async(self, prompt):
            raise RuntimeError("Unable to locate credentials")
            yield  # pragma: no cover

    app.agent = Failing([])
    asyncio.run(app.run_turn("hi"))
    out = app.console.export_text()
    assert "Error: Unable to locate credentials" in out
    assert "ANTHROPIC_API_KEY" in out


def _answer_permission(app, prompt, keys):
    async def run():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            pipe.send_text(keys)
            return await app._ask_permission(prompt)

    return asyncio.run(run())


def test_fileotter_tools_never_prompt(app):
    assert _answer_permission(app, 'Approve "convert_file"?\n  Input: {}', "") == "y"


@pytest.mark.parametrize(("keys", "expected"), [("1\r", "y"), ("2\r", "t"), ("bogus\r3\r", "n")])
def test_gated_tools_prompt(app, keys, expected):
    app.agent = FakeAgent([])
    answer = _answer_permission(app, 'Approve "shell"?\n  Input: {"command": "rm -rf build"}', keys)
    assert answer == expected
    out = app.console.export_text()
    assert "Shell command" in out and "rm -rf build" in out
    if expected == "n":
        assert app.agent.cancelled == {"after_current_tools": True}


def test_slash_commands(app):
    assert app._handle_command("/formats") is True
    assert "xlsx" in app.console.export_text()
    old = app.session_id
    app._handle_command("/clear")
    assert app.session_id != old
    assert app._handle_command("/exit") is False


def test_ctrl_c_interrupts_the_turn_not_the_app(app):
    import os
    import signal

    class Slow(FakeAgent):
        async def stream_async(self, prompt):
            yield {"data": "Working on it"}
            os.kill(os.getpid(), signal.SIGINT)  # user presses ctrl+c mid-turn
            for _ in range(50):
                await asyncio.sleep(0.01)
                if self.cancelled is not None:
                    return
                yield {"data": "."}

    app.agent = Slow([])
    asyncio.run(app.run_turn("go"))  # must return normally, not raise KeyboardInterrupt
    assert app.agent.cancelled == {}
    assert "Interrupted" in app.console.export_text()


def test_prompt_toolkit_markup_renders(app):
    """The toolbar and prompt are prompt_toolkit markup; an invalid color crashes the first redraw."""
    from prompt_toolkit.formatted_text import to_formatted_text
    from prompt_toolkit.styles import Style

    style = Style([])
    for fragments in (to_formatted_text(app._toolbar()),):
        for style_str, _text, *_ in fragments:
            style.get_attrs_for_style_str(style_str)  # raises ValueError on a bad color


def test_inspect_results_render_as_one_readable_line():
    from fileotter.tui import summarize_inspection

    raw = (
        '{"path": "/x/a.pdf", "size_bytes": 751591, "detected_format": "pdf",'
        ' "details": {"pages": 3, "has_text_layer": false}}'
    )
    assert summarize_inspection(raw) == "pdf · 734.0 KB · 3 pages · no text layer (scanned?)"
    sheets = '{"size_bytes": 10, "detected_format": "xlsx", "details": {"sheets": [{}, {}]}}'
    assert summarize_inspection(sheets) == "xlsx · 10 B · 2 sheets"
    assert summarize_inspection("Error: File not found") == "Error: File not found"


def test_describe_list_files():
    assert describe_tool_call("list_files", {}) == "List(.)"
    assert describe_tool_call("list_files", {"directory": "/x", "pattern": "*.pdf"}) == "List(/x *.pdf)"
