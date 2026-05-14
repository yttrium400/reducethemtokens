# Changelog

All notable changes to this project are documented here.

---

## [0.4.0] — 2026-05-14

### Added
- `--no-tests` flag on `rtt install`, `rtt update`, and `rtt index` — excludes test/spec/fixture files from the skeleton. On large repos like Django this alone cuts skeleton size by ~67%.
- `rtt install` and `rtt update` now print a tip when the skeleton exceeds 100k tokens and no size flag was used, pointing to `--no-tests` and `--max-tokens`.
- `rtt.index()` Python API now accepts `include`, `exclude`, `max_tokens`, and `no_tests` parameters.

---

## [0.3.0] — 2026-05-13

### Added
- `--include` / `--exclude` glob filtering on `rtt install`, `rtt update`, and `rtt index`. Example: `rtt install . --include 'src/**' --exclude 'vendor/**'`.
- `--max-tokens` budget cap: trims the skeleton to fit a token limit, keeping non-test files with the most symbols first.
- Staleness header in `.rtt/context.txt`: first two lines show the generation timestamp and file count so agents can detect an outdated index without reading the full file.
- `rtt update --diff`: shows which symbols were added or removed since the last update.
- CI/CD workflow documentation in README for teams where not every contributor has rtt installed.
- `tests/test_accuracy_controlled.py`: controlled accuracy test using Claude as judge (requires `ANTHROPIC_API_KEY`).

---

## [0.2.0] — 2026-05-12

### Added
- `rtt install` / `rtt uninstall`: injects orientation instructions into 9 agent config files (Claude Code, Cursor, Windsurf, Codex/OpenAI, GitHub Copilot, Kiro, Gemini CLI, Aider, Zed).
- Git pre-commit hook: `rtt install` writes `.git/hooks/pre-commit` so the skeleton auto-updates on every commit.
- `rtt update`: dedicated command to regenerate `.rtt/context.txt` without touching agent config files.
- `rtt vs`: compare rtt's token footprint against graphify side-by-side.

### Fixed
- JavaScript/TypeScript coverage raised from 34% to 97.9% on Django. Two root causes fixed:
  - IIFE wrappers `(function($){...})(jQuery)` were swallowing all top-level definitions.
  - Bare block statements `{ function foo(){} }` parsed as tree-sitter `ERROR` nodes, children were silently dropped.

---

## [0.1.0] — 2026-05-11

### Added
- Initial release.
- `rtt index`: extract imports, function signatures, class hierarchies, and method lists from Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, and Ruby using tree-sitter.
- `rtt compare`: show token reduction stats with per-file breakdown.
- `rtt bench`: heuristic benchmark measuring information retention (parameter names, return types, method lists, imports).
- `rtt audit`: symbol coverage and signature correctness verification.
- `rtt view`: render skeleton as markdown in a pager.
- Per-file content-hash cache for fast subsequent runs.
- `tiktoken` (cl100k_base) token counting.
- 91 tests, no network calls required.
