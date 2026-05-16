from rtt.installer import PLATFORM_BY_NAME, detect_platforms, install, uninstall


def test_continue_platform_is_registered():
    platform = PLATFORM_BY_NAME["continue"]

    assert platform.label == "Continue.dev"
    assert platform.config_path == ".continue/rules/rtt.md"
    assert platform.always_create is True
    assert platform.format == "markdown"


def test_install_and_uninstall_continue_platform(tmp_path):
    results = install(
        str(tmp_path),
        ["continue"],
        compressed_tokens=123,
        raw_tokens=456,
        reduction=73.0,
    )

    config = tmp_path / ".continue" / "rules" / "rtt.md"
    assert len(results) == 1
    assert results[0].platform == "continue"
    assert results[0].config_file == ".continue/rules/rtt.md"
    assert results[0].action == "created"
    assert config.exists()
    assert "<!-- rtt:start -->" in config.read_text(encoding="utf-8")

    uninstall_results = uninstall(str(tmp_path), ["continue"])

    assert len(uninstall_results) == 1
    assert uninstall_results[0].platform == "continue"
    assert not config.exists()


# ── detect_platforms tests ────────────────────────────────────────────────────

def test_detect_empty_repo(tmp_path):
    assert detect_platforms(str(tmp_path)) == []


def test_detect_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# test")
    detected = detect_platforms(str(tmp_path))
    assert detected == ["claude"]


def test_detect_claude_dir(tmp_path):
    (tmp_path / ".claude").mkdir()
    detected = detect_platforms(str(tmp_path))
    assert detected == ["claude"]


def test_detect_cursor(tmp_path):
    (tmp_path / ".cursor").mkdir()
    detected = detect_platforms(str(tmp_path))
    assert detected == ["cursor"]


def test_detect_windsurf_rules(tmp_path):
    (tmp_path / ".windsurfrules").write_text("# test")
    detected = detect_platforms(str(tmp_path))
    assert detected == ["windsurf"]


def test_detect_multiple_platforms(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# test")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / "AGENTS.md").write_text("# test")
    detected = detect_platforms(str(tmp_path))
    assert set(detected) == {"claude", "cursor", "codex"}


def test_detect_all_platforms(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# test")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".windsurfrules").write_text("# test")
    (tmp_path / "AGENTS.md").write_text("# test")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".kiro").mkdir()
    (tmp_path / "GEMINI.md").write_text("# test")
    (tmp_path / ".aider").mkdir()
    (tmp_path / ".continue").mkdir()
    (tmp_path / ".zed").mkdir()
    detected = detect_platforms(str(tmp_path))
    assert len(detected) == 10
