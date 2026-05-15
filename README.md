# rtt — reducethemtokens

[![PyPI](https://img.shields.io/pypi/v/reducethemtokens)](https://pypi.org/project/reducethemtokens/)
[![Python](https://img.shields.io/pypi/pyversions/reducethemtokens)](https://pypi.org/project/reducethemtokens/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Give any LLM a complete map of your codebase in a single, cheap read.

![rtt demo](demo.gif)

`rtt` extracts every file's imports, function signatures, class hierarchies, and method
lists into a compact plain-text skeleton — typically 90% smaller than the raw source —
and wires it into your agent's config so the map is available from the first message of
every session.

---

## The problem it solves

Modern coding agents (Cursor, Claude Code, Copilot) are good at retrieving context for
*specific, targeted queries*. Ask about one bug, one function, one file — they find it.

But they struggle with the **orientation problem**: starting a session on an unfamiliar
codebase, or asking questions that span the whole structure. Before an agent can retrieve
the right context, it needs to understand what exists and where. Without that map, it
either scans files speculatively (burning tokens) or makes wrong assumptions about
structure.

`rtt` solves this by providing that map upfront, once, cheaply. The agent reads the
skeleton at session start, knows the full API surface, and then opens only the specific
files it actually needs.

**rtt is not a replacement for agent retrieval.** Retrieval is better for targeted,
implementation-level tasks. rtt is the orientation layer that makes retrieval more
accurate by giving the agent the right mental model before it starts searching.

---

## When to use it

**Use rtt when:**

- Starting a session on a codebase the agent hasn't seen before
- The task involves cross-cutting changes across many files (a refactor, a rename, adding
  a feature that touches multiple layers)
- You're using a chat interface (ChatGPT, Claude.ai, direct API) that has no built-in
  retrieval — every session starts from zero
- You're building a CI pipeline, a code review bot, or any automated workflow where
  reproducible, deterministic context matters
- You want to give an LLM repo context without setting up a vector store or any
  additional infrastructure

**rtt is less useful when:**

- You're asking about one specific file or function — just open it
- Your agent already has full retrieval and you're working on targeted, well-scoped tasks

---

## Installation

```
pip install reducethemtokens
```

Requires Python 3.9+.

---

## Quick start

```
cd your-repo
rtt install .
```

This writes `.rtt/context.txt` (the skeleton) and adds a short instruction to every
supported agent config file — `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/`, and others.
The instruction tells the agent to read the skeleton at session start for orientation,
then work normally from there.

Commit both files. Every collaborator and every future session gets the map automatically.

```
# After code changes — regenerate the skeleton
rtt update .

# See how many tokens the skeleton saves vs raw source
rtt compare .
```

**Sample skeleton output for one file:**

```
# rtt/bench.py [python]
imports: os, random, re, dataclasses.dataclass, dataclasses.field, pathlib.Path, typing.Optional, rtt.RepoIndex, rtt.Symbol
class BenchQuestion
class QuestionResult
class BenchReport
  def heuristic_score(self) -> float
  def heuristic_by_kind(self) -> dict[str, tuple[int, int]]
  def heuristic_failing(self) -> list[QuestionResult]
  def llm_score(self) -> Optional[float]
def generate_questions(repo: RepoIndex) -> list[BenchQuestion]
def score_heuristic(questions: list[BenchQuestion], repo: RepoIndex) -> list[QuestionResult]
def run_bench(path: str, use_llm: bool, llm_sample: int) -> BenchReport
```

---

## Benchmark — Django (3,020 files)

| Metric | Value |
|---|---|
| Raw codebase | 6,464,961 tokens |
| rtt skeleton | 585,421 tokens |
| Reduction | **90.9%** |
| Heuristic bench score | **100.0%** (13,665 / 13,670 questions) |
| Audit coverage (Python) | **99.9%** (34,454 / 34,480 symbols) |
| Audit coverage (JavaScript) | **97.9%** (46 / 47 symbols) |

The heuristic bench auto-generates factual questions from the index — parameter names,
return types, method lists, imports — and verifies every answer appears in the skeleton.
100% means no structural information was lost in compression.

A separate controlled accuracy test asks Claude structural questions about code, once
with full source and once with the skeleton, then uses Claude-as-judge to verify
correctness. The skeleton scores **90%** on this test — matching the full source on
every question except those involving imports defined inside function bodies (a
structural limitation: rtt only captures top-level imports by design).

---

## Commands

### `rtt install`

Index the repo, write the skeleton to `.rtt/context.txt`, and inject orientation
instructions into every supported agent config file. Also installs a git pre-commit hook
that regenerates the skeleton automatically on every commit.

```
rtt install .
rtt install . --platform claude    # single agent only
rtt install . --force              # overwrite existing rtt sections
rtt install . --no-tests           # exclude test/spec/fixture files
rtt install . --max-tokens 100000  # trim to fit a context window budget
rtt install . --include 'src/**'   # only index specific directories
rtt install . --exclude 'vendor/**'
```

Supported agents:

| Agent | Config file |
|---|---|
| Claude Code | `CLAUDE.md` |
| Cursor | `.cursor/rules/rtt.mdc` |
| Windsurf | `.windsurfrules` |
| Codex / OpenAI | `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Kiro | `.kiro/steering/rtt.md` |
| Gemini CLI | `GEMINI.md` |
| Aider | `.aider/prompts/conventions.md` |
| Zed | `.rules` |

The instruction added to each config file tells the agent to read `.rtt/context.txt`
once at session start for orientation, then work normally. It does not restrict the
agent from reading source files or using its own retrieval.

### `rtt update`

Regenerate `.rtt/context.txt` after code changes. Does not touch agent config files.
The git hook installed by `rtt install` runs this automatically on every commit.

```
rtt update .
rtt update . --diff        # show what symbols changed
rtt update . --no-tests    # same flags as install are accepted
```

### `rtt uninstall`

Remove rtt instructions from all agent config files.

```
rtt uninstall .
rtt uninstall . --platform cursor
rtt uninstall . --clean    # also delete .rtt/context.txt
```

### `rtt index`

Generate the skeleton and print to stdout, or write to a file. Useful for piping
into other tools or building custom workflows.

```
rtt index .
rtt index /path/to/repo --output context.txt
rtt index . --no-tests
rtt index . --include 'src/**' --include 'lib/**'
rtt index . --exclude 'vendor/**' --exclude 'generated/**'
rtt index . --max-tokens 50000
```

### `rtt compare`

Show token reduction statistics with a per-file breakdown.

```
rtt compare .
rtt compare . --diff HEAD~3..HEAD    # token delta for a git range
```

### `rtt bench`

Measure how much structural information the skeleton retains.

```
rtt bench .                        # heuristic only, free, instant
rtt bench . --llm --sample 30      # semantic equivalence via Claude
```

The `--llm` mode requires `ANTHROPIC_API_KEY` and `pip install "reducethemtokens[llm]"`.

### `rtt audit`

Verify extraction accuracy: symbols found vs expected, and signature correctness.

```
rtt audit .
```

### `rtt vs`

Compare token footprint against another repo-indexing tool (currently supports graphify).

```
pip install graphifyy
rtt vs .
```

### `rtt view`

Render the skeleton as markdown and open in a pager.

```
rtt view .
rtt view . --output overview.md
```

---

## Keeping the skeleton current

`rtt install` sets up a git pre-commit hook that runs `rtt update` automatically
on every commit. For most solo workflows that is enough.

**For teams**, the hook only runs on machines where rtt is installed. A new
contributor who clones the repo without installing rtt will not regenerate the
skeleton. Two approaches:

Add a CI step that regenerates and commits the skeleton on every merge to main:

```yaml
# .github/workflows/rtt.yml
name: Update rtt index
on:
  push:
    branches: [main]
jobs:
  rtt:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install reducethemtokens
      - run: rtt update .
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "chore: update rtt index"
          file_pattern: ".rtt/context.txt"
```

Or document it in your contributing guide:

```
# after pulling changes
rtt update .
git add .rtt/context.txt
```

The first line of `.rtt/context.txt` includes the generation timestamp and file
count, so agents can detect a stale index without reading the whole file.

**Large repos and context window limits**

Test files are usually the biggest contributor to skeleton size. The simplest
reduction for most projects is `--no-tests`:

```
rtt install . --no-tests    # drops test/, spec/, fixture/ files
```

On Django (3,020 files), this alone cuts the skeleton from 585k tokens to 193k.

If the skeleton is still too large, use `--max-tokens` to trim it to fit.
rtt keeps non-test files with the most symbols and drops the rest:

```
rtt install . --max-tokens 100000    # fits in most 128k-window models
rtt install . --max-tokens 50000     # conservative
```

A rough guide by repo size:

| Repo scale | Approach |
|---|---|
| < 500 files | no flag needed |
| 500–2,000 files | `--no-tests` |
| 2,000+ files (e.g. Django) | `--no-tests` + `--max-tokens 100000` |

Or limit to specific directories:

```
rtt install . --include 'src/**' --include 'lib/**'
rtt install . --exclude 'vendor/**' --exclude 'generated/**'
```

---

## Python API

```python
import rtt

repo = rtt.index("/path/to/repo")
repo = rtt.index("/path/to/repo", no_tests=True)
repo = rtt.index("/path/to/repo", max_tokens=100000)
repo = rtt.index("/path/to/repo", include=["src/**"], exclude=["vendor/**"])

print(repo.token_count)    # int
print(repo.text)           # full skeleton as a string

for file in repo.files:
    print(file.path, file.language)
    print(file.imports)    # e.g. ["pathlib.Path", "typing.Optional"]
    for sym in file.symbols:
        print(sym.name, sym.kind, sym.signature)
        for child in sym.children:
            print(" ", child.signature)

report = rtt.compare("/path/to/repo")
print(f"{report.reduction_pct:.1f}% reduction")
print(f"{report.raw_tokens:,} → {report.compressed_tokens:,} tokens")
```

---

## Supported languages

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby.

---

## How it works

`rtt` parses each file with [tree-sitter](https://tree-sitter.github.io/tree-sitter/)
and walks the AST to collect top-level definitions: functions, classes, methods, and
imports. Function bodies are discarded. The output is one line per symbol, indented to
show class membership, with imports resolved to specific symbols
(`from pathlib import Path` → `pathlib.Path`).

Results are cached by file content hash. Subsequent runs on large repos are fast.

---

## Development

```
git clone https://github.com/yttrium400/reducethemtokens
cd reducethemtokens
pip install -e ".[dev]"
pytest tests/
```

91 tests. No network calls required.

---

## License

MIT
