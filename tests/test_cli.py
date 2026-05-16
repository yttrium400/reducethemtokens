"""CLI integration tests using typer's CliRunner."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rtt.cli import app

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, rel: str, content: str = "") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _make_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "main.py", "def hello(name: str) -> str:\n    return f'hi {name}'\n")
    _write(tmp_path, "utils.py", "import os\n\ndef read_file(path: str) -> str:\n    return open(path).read()\n")
    return tmp_path


# ── rtt index ─────────────────────────────────────────────────────────────────

class TestIndexCommand:
    def test_index_text_output(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["index", str(tmp_path)])
        assert result.exit_code == 0
        assert "main.py" in result.stdout
        assert "hello" in result.stdout

    def test_index_json_output(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["index", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "files" in data
        paths = [f["path"] for f in data["files"]]
        assert any("main.py" in p for p in paths)

    def test_index_json_structure(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["index", str(tmp_path), "--format", "json"])
        data = json.loads(result.stdout)
        for f in data["files"]:
            assert "path" in f
            assert "language" in f
            assert "imports" in f
            assert "symbols" in f

    def test_index_json_symbols_have_fields(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["index", str(tmp_path), "--format", "json"])
        data = json.loads(result.stdout)
        main_file = next(f for f in data["files"] if "main.py" in f["path"])
        sym = main_file["symbols"][0]
        assert "name" in sym
        assert "kind" in sym
        assert "signature" in sym
        assert "children" in sym

    def test_index_invalid_format(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["index", str(tmp_path), "--format", "xml"])
        assert result.exit_code == 1

    def test_index_invalid_path(self, tmp_path):
        result = runner.invoke(app, ["index", str(tmp_path / "nonexistent")])
        assert result.exit_code == 1

    def test_index_output_file(self, tmp_path):
        _make_repo(tmp_path)
        out = tmp_path / "out.txt"
        result = runner.invoke(app, ["index", str(tmp_path), "--output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "main.py" in out.read_text()

    def test_index_output_file_json(self, tmp_path):
        _make_repo(tmp_path)
        out = tmp_path / "out.json"
        result = runner.invoke(app, ["index", str(tmp_path), "--format", "json", "--output", str(out)])
        assert result.exit_code == 0
        data = json.loads(out.read_text())
        assert "files" in data

    def test_index_no_tests_excludes_test_files(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "tests/test_main.py", "def test_hello(): assert hello('x') == 'hi x'\n")
        result_all = runner.invoke(app, ["index", str(tmp_path)])
        result_no_tests = runner.invoke(app, ["index", str(tmp_path), "--no-tests"])
        assert "test_main" in result_all.stdout
        assert "test_main" not in result_no_tests.stdout

    def test_index_include_filter(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "src/core.py", "def core_fn(): pass\n")
        result = runner.invoke(app, ["index", str(tmp_path), "--include", "src/**"])
        assert result.exit_code == 0
        assert "core_fn" in result.stdout
        assert "hello" not in result.stdout

    def test_index_exclude_filter(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "vendor/lib.py", "def vendor_fn(): pass\n")
        result = runner.invoke(app, ["index", str(tmp_path), "--exclude", "vendor/**"])
        assert result.exit_code == 0
        assert "vendor_fn" not in result.stdout

    def test_index_max_tokens(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["index", str(tmp_path), "--max-tokens", "10"])
        assert result.exit_code == 0

    def test_index_empty_repo(self, tmp_path):
        result = runner.invoke(app, ["index", str(tmp_path)])
        assert result.exit_code == 0

    def test_index_json_empty_repo(self, tmp_path):
        result = runner.invoke(app, ["index", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data == {"files": []}


# ── rtt install - auto-detection ──────────────────────────────────────────────

class TestInstallAutoDetect:
    def test_install_detects_claude_md(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "CLAUDE.md", "# Claude instructions\n")
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert result.exit_code == 0
        assert "claude" in result.stdout
        assert "Detected" in result.stdout

    def test_install_detects_cursor_dir(self, tmp_path):
        _make_repo(tmp_path)
        (tmp_path / ".cursor").mkdir()
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert result.exit_code == 0
        assert "cursor" in result.stdout

    def test_install_detects_multiple_platforms(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "CLAUDE.md", "# Claude\n")
        _write(tmp_path, "AGENTS.md", "# Codex\n")
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert result.exit_code == 0
        assert "claude" in result.stdout
        assert "codex" in result.stdout

    def test_install_all_flag_skips_detection(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "CLAUDE.md", "# Claude\n")
        result = runner.invoke(app, ["install", str(tmp_path), "--all"])
        assert result.exit_code == 0
        assert "Detected" not in result.stdout

    def test_install_all_writes_to_all_platforms(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["install", str(tmp_path), "--all"])
        assert result.exit_code == 0
        assert (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / "GEMINI.md").exists()

    def test_install_no_platforms_detected_falls_back_to_all(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert result.exit_code == 0
        assert "No platforms detected" in result.stdout

    def test_install_explicit_platform_overrides_detection(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, "CLAUDE.md", "# Claude\n")
        _write(tmp_path, "AGENTS.md", "# Codex\n")
        result = runner.invoke(app, ["install", str(tmp_path), "--platform", "gemini"])
        assert result.exit_code == 0
        assert (tmp_path / "GEMINI.md").exists()
        assert "Detected" not in result.stdout

    def test_install_invalid_platform(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["install", str(tmp_path), "--platform", "vscode"])
        assert result.exit_code == 1

    def test_install_writes_skeleton(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["install", str(tmp_path), "--all"])
        skel = tmp_path / ".rtt" / "context.txt"
        assert skel.exists()
        assert "main.py" in skel.read_text()

    def test_install_force_overwrites(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["install", str(tmp_path), "--platform", "claude"])
        (tmp_path / "CLAUDE.md").write_text("# My existing notes\n\n<!-- rtt:start -->\nold content\n<!-- rtt:end -->\n")
        result = runner.invoke(app, ["install", str(tmp_path), "--platform", "claude", "--force"])
        assert result.exit_code == 0
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "My existing notes" in content
        assert "old content" not in content
        assert "rtt:start" in content

    def test_install_skips_without_force(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["install", str(tmp_path), "--platform", "claude"])
        result = runner.invoke(app, ["install", str(tmp_path), "--platform", "claude"])
        assert "skipped" in result.stdout

    def test_install_detects_windsurf_rules_file(self, tmp_path):
        _make_repo(tmp_path)
        _write(tmp_path, ".windsurfrules", "# windsurf\n")
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert "windsurf" in result.stdout

    def test_install_detects_github_dir_for_copilot(self, tmp_path):
        _make_repo(tmp_path)
        (tmp_path / ".github").mkdir()
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert "copilot" in result.stdout

    def test_install_detects_continue_dir(self, tmp_path):
        _make_repo(tmp_path)
        (tmp_path / ".continue").mkdir()
        result = runner.invoke(app, ["install", str(tmp_path)])
        assert "continue" in result.stdout


# ── rtt update ────────────────────────────────────────────────────────────────

class TestUpdateCommand:
    def test_update_creates_skeleton(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["update", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / ".rtt" / "context.txt").exists()

    def test_update_refreshes_skeleton(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["update", str(tmp_path)])
        _write(tmp_path, "new_module.py", "def brand_new(): pass\n")
        runner.invoke(app, ["update", str(tmp_path)])
        content = (tmp_path / ".rtt" / "context.txt").read_text()
        assert "brand_new" in content

    def test_update_diff_no_changes(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["update", str(tmp_path)])
        result = runner.invoke(app, ["update", str(tmp_path), "--diff"])
        assert result.exit_code == 0
        assert "No structural changes" in result.stdout

    def test_update_diff_shows_added(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["update", str(tmp_path)])
        _write(tmp_path, "new_module.py", "def brand_new(): pass\n")
        result = runner.invoke(app, ["update", str(tmp_path), "--diff"])
        assert result.exit_code == 0
        assert "added" in result.stdout

    def test_update_warns_if_no_skeleton(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["update", str(tmp_path)])
        assert result.exit_code == 0


# ── rtt uninstall ─────────────────────────────────────────────────────────────

class TestUninstallCommand:
    def test_uninstall_removes_section(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["install", str(tmp_path), "--platform", "claude"])
        assert "rtt:start" in (tmp_path / "CLAUDE.md").read_text()
        result = runner.invoke(app, ["uninstall", str(tmp_path), "--platform", "claude"])
        assert result.exit_code == 0
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_uninstall_nothing_installed(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["uninstall", str(tmp_path)])
        assert result.exit_code == 0
        assert "No rtt sections" in result.stdout

    def test_uninstall_clean_removes_skeleton(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["install", str(tmp_path), "--platform", "claude"])
        result = runner.invoke(app, ["uninstall", str(tmp_path), "--clean"])
        assert result.exit_code == 0
        assert not (tmp_path / ".rtt" / "context.txt").exists()

    def test_uninstall_invalid_platform(self, tmp_path):
        _make_repo(tmp_path)
        result = runner.invoke(app, ["uninstall", str(tmp_path), "--platform", "vscode"])
        assert result.exit_code == 1

    def test_uninstall_specific_platform_leaves_others(self, tmp_path):
        _make_repo(tmp_path)
        runner.invoke(app, ["install", str(tmp_path), "--all"])
        runner.invoke(app, ["uninstall", str(tmp_path), "--platform", "claude"])
        assert not (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / "GEMINI.md").exists()
