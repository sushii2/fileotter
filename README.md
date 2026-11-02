# Fileotter

Convert and compress any file on your system by asking for it in plain language.
Fileotter is a [Strands harness](https://strandsagents.com/docs/user-guide/harness/) agent with
its own conversion and compression engines. Your files never leave your machine.

```bash
uv sync
uv run fileotter                                   # interactive, like Claude Code
uv run fileotter "turn ~/Downloads/invoice.pdf into a Word doc"
uv run fileotter -p "inspect ~/Desktop/mystery"    # one request, then exit
uv run fileotter --formats                         # what can convert to what
```

In the app: `/help`, `/formats`, `/model`, `/clear`, `/session`, `/exit`. **Tab** completes file
paths, `@path` mentions a file, **⌥⏎** adds a newline, and **Ctrl-C** interrupts a turn. You can also
drag a file from Finder into the terminal. Fileotter asks before running shell commands or editing
files. Conversations are saved, so you can resume one with `--session <id>`.

## Model setup

Put your key in a `.env` file. Fileotter looks in the current folder (and its parents), then in
`~/.fileotter/.env`, which works from any folder:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

With an Anthropic key, Fileotter uses `claude-sonnet-5-5`. To choose another model, set
`FILEOTTER_MODEL` (e.g. `anthropic/claude-opus-5-5`, `openai/gpt-5.4`) or pass `--model`. Without any
key it falls back to Amazon Bedrock and your AWS credentials.

## What it supports today

- **Documents:** PDF → DOCX/TXT/MD, DOCX → TXT/MD/XLSX/CSV (tables), TXT/MD → DOCX
- **Spreadsheets/data:** CSV ↔ TSV ↔ XLSX ↔ JSON
- **Images:** PNG, JPG, WEBP, GIF, BMP, TIFF to each other, and any of them → PDF
- **Compression:** zip, tar.gz, tar.bz2, tar.xz, gz. Safe extraction. Image recompression and resizing.
- **PDF compression:** downsamples and re-encodes embedded images. Text and vectors are untouched.
- **Excel compression:** recompresses the workbook and its embedded images. Data, formulas, charts, and macros are kept exactly.
- **Inspection:** detects a file's real type, summarizes its contents, and flags scanned PDFs and wrong extensions.

See [AGENTS.md](AGENTS.md) for architecture, how to add a format, and the roadmap.
