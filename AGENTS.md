# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What Fileotter is

Fileotter lets anyone convert any file on their system to another format (PDF → Word,
Word → Excel/CSV, CSV ↔ XLSX ↔ JSON, image ↔ image, image → PDF, …) and compress or extract
files. A user describes what they want in plain language. An agent built on the
[Strands harness](https://strandsagents.com/docs/user-guide/harness/) runs the request using
Fileotter's own conversion and compression engines.

**Core principle: built in house, local-first.** Every conversion and compression runs on the
user's machine with local Python libraries. Never add a dependency or tool that sends user files
or their contents to a third-party conversion API or website. This is also why the agent's
`web_fetch` / `web_search` built-ins are disabled.

## Layout

```
src/fileotter/
  __init__.py          CLI entry point (`fileotter` script): args -> TUI, --print, --formats
  tui.py               Interactive terminal UI (Claude Code style): streaming, tool lines, approvals
  agent.py             build_agent(): create_harness(...) + INSTRUCTIONS + GATED_TOOLS + state dir
  tools.py             @tool adapters exposing the engines to the agent (ALL_TOOLS)
  compression.py       Archives (zip, tar.gz/bz2/xz, gz) + image, PDF, and xlsx compression
  inspection.py        inspect_file(): magic-byte type detection + per-format content summary
  converters/
    registry.py        @converter decorator, format normalization/aliases, convert()
    documents.py       pdf, docx, txt, md
    tabular.py         csv, tsv, xlsx, json (all pairs generated from reader/writer tables)
    images.py          png, jpg, webp, gif, bmp, tiff (+ → pdf) via Pillow
tests/                 pytest; engine tests need no model credentials
```

Runtime state (sessions, memory, skills, prompt history, `fileotter.log`) lives in `~/.fileotter/`
(override with `$FILEOTTER_HOME`), never in the cwd, because users run Fileotter from any folder.

## Commands

Use `uv` for everything. Do not use pip directly.

```bash
uv sync                          # install deps (add --extra anthropic|openai|gemini|ollama for other providers)
uv run pytest -q                 # run tests
uv run ruff check --fix src tests && uv run ruff format src tests
uv run fileotter --formats       # print the supported conversion matrix
uv run fileotter                 # interactive TUI
uv run fileotter "convert ~/Downloads/report.pdf to word"   # TUI with a first message
uv run fileotter -p "inspect ~/a.pdf"                       # one request, then exit
uv run fileotter --session fo-1234abcd                      # resume a conversation
```

API keys load from `.env` (cwd and parents, then `~/.fileotter/.env`; the shell environment wins).
Model selection: `--model provider/name` flag, else `$FILEOTTER_MODEL`, else `anthropic/claude-sonnet-5-5`
when `ANTHROPIC_API_KEY` is set, else the harness default
(Amazon Bedrock, needs AWS credentials). The Anthropic SDK is a default dependency. Other providers need
`uv sync --extra openai|gemini|ollama`.

## Architecture rules

1. **Engines and agent stay separate.** `converters/`, `compression.py`, and `inspection.py` are plain
   Python with no Strands imports. They take `Path`s and raise `ConversionError` / `CompressionError` /
   `InspectionError`. `tools.py` is
   the only place they meet the agent. Keep tools thin: parse args, call the engine, and return a
   short string. Return engine errors as `"Error: ..."` strings instead of raising, so the model can
   recover.
2. **Adding a conversion:** write a `(src: Path, dst: Path) -> None` function in the right
   `converters/` module (or a new module imported in `converters/__init__.py`) and decorate it with
   `@converter(("src", "dst"))`. It appears automatically in `list_supported_conversions`,
   `--formats`, and `convert_file`. Add a test that creates a real input file in `tmp_path` and checks
   the output's content, not just that it exists.
3. **Format ids** are lowercase extensions without the dot (`docx`, `xlsx`, `jpg`). Add user-facing
   synonyms to `ALIASES` in `registry.py`. Do not add special cases in converters.
4. **Never destroy user data.** Outputs never overwrite the source file. Existing outputs are only
   replaced with an explicit `overwrite=True`. Archive extraction must reject path traversal (zip-slip
   and tar `filter="data"`). Keep and test these guarantees.
5. **Be honest about fidelity.** If a converter drops information (for example, PDF → DOCX keeps text
   only, without layout or images, and XLSX → CSV keeps only the first sheet), say so in its docstring.
   The agent relays this to users.
6. **Harness configuration** belongs in `agent.build_agent()`. Override harness defaults there
   instead of around `create_harness`. Before using a harness option, check it in the docs
   (Strands MCP server or https://strandsagents.com/docs/user-guide/harness/reference/configuration/)
   or in the installed source at `.venv/lib/python3.13/site-packages/strands_harness/`. The API is
   new and changes often, so do not rely on memory.

## Known gaps / roadmap

- DOCX/XLSX → PDF (needs a layout engine; keep it in-house, e.g. a reportlab-based renderer).
- PDF → XLSX/CSV table extraction.
- Higher-fidelity PDF → DOCX (layout, images).
- PPTX, ODT/ODS, audio/video (would need ffmpeg as a local binary).

## Terminal UI (`tui.py`)

- Renders `agent.stream_async` events itself (the agent gets `callback_handler=None`): `data` → streaming
  Markdown, assistant `toolUse` blocks → `⏺ Label(summary)` lines, `toolResult` blocks → `⎿` previews.
  When adding a tool, add a friendly label to `_TOOL_LABELS` and a summary rule in
  `describe_tool_call` if its main argument isn't a `*path`.
- Approvals use the SDK's `HumanInTheLoop` with an inline async `ask`. Only `GATED_TOOLS` (shell, write,
  edit, code execution) prompt. Fileotter's own tools are safe by construction and never prompt.
  Option 2 ("don't ask again") is the handler's session trust. "No" denies and stops the turn.
- The UX is conversational ("compress this file", "convert it and zip it"). The agent resolves vague
  references from the conversation, the working directory (injected into `INSTRUCTIONS` as `{cwd}`), and
  `list_files`. It must never need the gated shell just to look around, so finding and identifying files
  stays in read-only Fileotter tools. Tool path args go through `tools._path()`, which accepts pasted or
  dragged-in paths (quotes, `file://`, backslash-escaped spaces).
- Ctrl-C during a turn calls `agent.cancel()` through an asyncio SIGINT handler. At the prompt,
  prompt_toolkit handles it (press twice to exit).
- Test the TUI with the fake-agent pattern in `tests/test_tui.py`. Never call a real model in tests.

## Compression and inspection notes

- `compress_pdf` touches only embedded raster images (RGB/L/CMYK). It skips images with alpha, palette,
  or bilevel images, because JPEG would break them. It replaces an image only when the re-encoded
  version is smaller. Text, fonts, and vectors are never re-encoded.
- `compress_xlsx` rewrites the zip container part by part and must stay byte-for-byte for everything
  outside `xl/media/`. Never round-trip a workbook through openpyxl to compress it: openpyxl drops
  charts, pivot caches, and other parts it doesn't model.
- `inspect_file` detects type from magic bytes. Text formats can't be sniffed, so they are identified by
  extension. When adding a format, add a describer to `_DESCRIBERS` and, if it can be compressed, an
  entry in `_COMPRESSION_TOOL`.

## Conventions

- Python 3.13, type hints everywhere, `from __future__ import annotations`.
- Ruff (line length 120) is the linter and formatter. Code should pass `ruff check` and `pytest`
  before you finish.
- Tests must not need network or model credentials, except `tests/test_agent.py`, which builds the
  agent offline and makes no model calls. Set `FILEOTTER_HOME` to a tmp dir in any test that builds an agent.
