from rtt.installer import PLATFORM_BY_NAME, install, uninstall


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
