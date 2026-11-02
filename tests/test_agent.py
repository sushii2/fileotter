from fileotter.agent import build_agent


def test_agent_registers_fileotter_tools_and_no_web(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep harness session/memory dirs out of the repo
    monkeypatch.delenv("FILEOTTER_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("FILEOTTER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AWS_REGION", "us-east-1")  # Bedrock default; no call is made at construction
    agent = build_agent()
    names = set(agent.tool_names)
    assert {
        "list_files",
        "convert_file",
        "compress_files",
        "extract_archive",
        "compress_image",
        "list_supported_conversions",
        "inspect_file",
        "compress_pdf",
        "compress_xlsx",
    } <= names
    assert not names & {"web_fetch", "web_search"}


def test_model_resolution_order(monkeypatch):
    from fileotter.agent import DEFAULT_ANTHROPIC_MODEL, resolve_model

    monkeypatch.delenv("FILEOTTER_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve_model() is None  # harness default: Bedrock
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert resolve_model() == DEFAULT_ANTHROPIC_MODEL
    monkeypatch.setenv("FILEOTTER_MODEL", "openai/gpt-5.4")
    assert resolve_model() == "openai/gpt-5.4"
    assert resolve_model("ollama/llama3.1") == "ollama/llama3.1"


def test_instructions_know_the_working_directory(tmp_path, monkeypatch):
    from fileotter.agent import INSTRUCTIONS

    assert "{cwd}" in INSTRUCTIONS
    assert str(tmp_path) in INSTRUCTIONS.format(cwd=tmp_path)
