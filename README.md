# rtt — reducethemtokens

Compress any code repository into a compact structural skeleton for use as LLM context.

Instead of sending tens of thousands of lines of source code to a language model, `rtt` extracts only what matters — function signatures, class hierarchies, method lists, and imports — and formats it as dense, readable plain text. The result is typically 85–95% smaller than the raw codebase while retaining 100% of the structural information a model needs to understand your API.

---

## Why

Large language models have finite context windows. When you want a model to help with a codebase it has never seen, you either paste in raw files and burn most of your token budget on implementation details the model doesn't need, or you summarize manually and risk missing things.

`rtt` automates the summarization step. It gives the model a complete map of every file — what it imports, what it defines, and how those definitions are shaped — without any of the function bodies.

---

## Installation

```
pip install reducethemtokens
```

Requires Python 3.9+.

For the LLM evaluation mode (optional):

```
pip install "reducethemtokens[llm]"
```

---

## Quick start

```
# Index the repo and wire it into every agent config automatically
rtt install .
```

That single command writes `.rtt/context.txt` and updates `CLAUDE.md`, `AGENTS.md`,
`.cursor/rules/`, and every other supported agent config. From that point on, any
coding agent working in this repo reads the skeleton at session start instead of
scanning raw source files.

```
# Re-run after code changes to keep the skeleton current
rtt install . --force

# If you only want the skeleton file without touching agent configs
rtt index . --output context.txt

# Show how many tokens you save
rtt compare .
```

**Sample output** for a single file:

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
def score_llm(questions: list[BenchQuestion], repo: RepoIndex, repo_path: str, sample_size: int) -> list[LLMQuestionResult]
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

The heuristic bench auto-generates factual questions from the index — parameter names, return types, method lists, imports — and verifies each answer appears in the skeleton. A score of 100% means no structural information was lost in compression.

---

## Commands

### `rtt install`

The primary command. Indexes the repo, writes the skeleton to `.rtt/context.txt`, and
injects instructions into every supported agent config file telling it to read that file
at the start of each session — before opening any source files.

```
rtt install .
rtt install /path/to/repo
rtt install . --platform claude    # single agent only
rtt install . --force              # overwrite existing rtt sections
```

Supported agents and the files they write to:

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

Commit `.rtt/context.txt` and the updated config files to your repository. Every
collaborator and every new session then gets the context automatically.

Re-run `rtt install` after significant code changes to keep the skeleton current.

### `rtt uninstall`

Remove rtt instructions from agent config files.

```
rtt uninstall .
rtt uninstall . --platform cursor   # single agent only
rtt uninstall . --clean             # also delete .rtt/context.txt
```

### `rtt index`

Generate the skeleton and print to stdout, or write to a file.

```
rtt index .
rtt index /path/to/repo --output context.txt
rtt index . --no-cache
```

### `rtt compare`

Show token reduction statistics for a repo, with a per-file breakdown of the largest files.

```
rtt compare .
rtt compare /path/to/repo --top 20
```

Compare token counts before and after a specific git commit range:

```
rtt compare . --diff HEAD~3..HEAD
```

### `rtt bench`

Measure how much structural information the skeleton retains. Runs entirely locally with no API calls by default.

```
rtt bench .
rtt bench . --show-failing
```

With `--llm`, sends sampled questions to Claude using full source and skeleton context, then uses a second model as a judge to measure semantic equivalence:

```
rtt bench . --llm --sample 30
```

Requires `ANTHROPIC_API_KEY` and `pip install "reducethemtokens[llm]"`.

### `rtt audit`

Verify extraction accuracy against the raw source. Compares symbols found by `rtt` against a full AST walk of each file to compute coverage, and checks that every extracted signature is syntactically well-formed.

```
rtt audit .
rtt audit . --show-passing
```

### `rtt view`

Render the skeleton as human-readable markdown and open it in a pager.

```
rtt view .
rtt view . --output overview.md
```

### `rtt vs`

Compare `rtt`'s token footprint against another repo-indexing tool side by side.

```
rtt vs .
```

Currently supports [graphify](https://pypi.org/project/graphifyy/). Install it first:

```
pip install graphifyy
rtt vs /path/to/repo
```

---

## Python API

`rtt` is also importable as a library:

```python
import rtt

# Index a repo
repo = rtt.index("/path/to/repo")

print(repo.token_count)          # int — token count of the skeleton
print(repo.text)                  # str — the full skeleton text

for file in repo.files:
    print(file.path, file.language)
    print(file.imports)           # list[str] — e.g. ["pathlib.Path", "os"]
    for sym in file.symbols:
        print(sym.name, sym.kind, sym.signature)
        for child in sym.children:   # methods inside a class
            print(" ", child.signature)

# Token comparison report
report = rtt.compare("/path/to/repo")
print(f"{report.reduction_pct:.1f}% reduction")
print(f"{report.raw_tokens:,} → {report.compressed_tokens:,} tokens")
```

---

## Supported languages

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, Ruby.

---

## How it works

`rtt` parses each file with [tree-sitter](https://tree-sitter.github.io/tree-sitter/) and walks the resulting AST to collect:

- Top-level function and class definitions, including those inside `try/except` or conditional blocks
- Class hierarchies with method signatures
- Import statements, resolved to the specific symbols being imported (`from pathlib import Path` becomes `pathlib.Path`)
- First-line docstrings where present

Function bodies are discarded entirely. The output is formatted as indented plain text — one line per symbol — optimized for token density rather than human readability, though it is readable.

Results are cached per-file by content hash, so subsequent runs on large repos are fast.

---

## Development

```
git clone https://github.com/yttrium400/reducethemtokens-rtt-
cd reducethemtokens-rtt-
pip install -e ".[dev]"
pytest tests/
```

The test suite covers extraction correctness across all supported languages, audit accuracy, and the full benchmark pipeline. 91 tests, no network calls required.

---

## License

MIT
