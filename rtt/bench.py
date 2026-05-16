import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rtt import RepoIndex, Symbol
from rtt.extractor import extract_repo
from rtt.formatter import format_file_text


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class BenchQuestion:
    kind: str          # "params" | "return_type" | "methods" | "imports"
    question: str      # natural-language question text
    file: str          # relative path to the source file
    symbol: str        # function / class name (empty for "imports")
    expected_terms: list[str]   # all must appear in skeleton for PASS


@dataclass
class QuestionResult:
    question: BenchQuestion
    passed: bool
    found_terms: list[str]
    missing_terms: list[str]


@dataclass
class LLMQuestionResult:
    question: str
    full_answer: str
    skeleton_answer: str
    equivalent: bool
    reasoning: str


@dataclass
class BenchReport:
    path: str
    total_questions: int
    heuristic_results: list[QuestionResult] = field(default_factory=list)
    llm_results: list[LLMQuestionResult] = field(default_factory=list)

    @property
    def heuristic_score(self) -> float:
        if not self.heuristic_results:
            return 100.0
        return sum(1 for r in self.heuristic_results if r.passed) / len(self.heuristic_results) * 100

    @property
    def heuristic_by_kind(self) -> dict[str, tuple[int, int]]:
        """Returns {kind: (passed, total)} for each question kind."""
        out: dict[str, list[int]] = {}
        for r in self.heuristic_results:
            if r.question.kind not in out:
                out[r.question.kind] = [0, 0]
            out[r.question.kind][1] += 1
            if r.passed:
                out[r.question.kind][0] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    @property
    def heuristic_failing(self) -> list[QuestionResult]:
        return [r for r in self.heuristic_results if not r.passed]

    @property
    def llm_score(self) -> Optional[float]:
        if not self.llm_results:
            return None
        return sum(1 for r in self.llm_results if r.equivalent) / len(self.llm_results) * 100

    @property
    def llm_failing(self) -> list[LLMQuestionResult]:
        return [r for r in self.llm_results if not r.equivalent]


# ── question generation ───────────────────────────────────────────────────────

def _flatten(symbols: list[Symbol]) -> list[Symbol]:
    out: list[Symbol] = []
    for s in symbols:
        out.append(s)
        out.extend(_flatten(s.children))
    return out


def _extract_outer_params(sig: str) -> str:
    """Return the content between the outermost () in the signature."""
    start = sig.find('(')
    if start == -1:
        return ""
    depth = 0
    for i, c in enumerate(sig[start:], start):
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return sig[start + 1:i]
    return sig[start + 1:]


def _split_params(params_block: str) -> list[str]:
    """Split a params string by top-level commas, respecting nested brackets."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for c in params_block:
        if c in ('(', '[', '{'):
            depth += 1
            current.append(c)
        elif c in (')', ']', '}'):
            depth -= 1
            current.append(c)
        elif c == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(c)
    if current:
        last = ''.join(current).strip()
        if last:
            parts.append(last)
    return parts


def _param_names(sig: str) -> list[str]:
    """Extract parameter names from a function signature, handling nested parens."""
    params_block = _extract_outer_params(sig)
    if not params_block:
        return []
    names = []
    for part in _split_params(params_block):
        part = part.strip().lstrip('*')
        # Take only the identifier before the first : = or whitespace
        name = re.split(r'[:=\s]', part)[0].strip()
        if name and name.isidentifier() and name not in ('self', 'cls'):
            names.append(name)
    return names


# Only filter return types that carry no information at all.
_SKIP_RETURN = frozenset({'None', 'Self', 'self', '...', ''})


def _return_type(sig: str) -> Optional[str]:
    """Extract the return type token from a function signature.

    Handles arrow style (Python/Rust/TS: -> Type) and
    post-paren style (Go: func foo() ReturnType).
    """
    # Arrow style: def fn() -> ReturnType
    if '->' in sig:
        ret = sig.split('->', 1)[1].strip().rstrip(':').strip()
        token = re.split(r'[\s\[\],()]', ret)[0].strip()
        return token if token and token not in _SKIP_RETURN else None

    # Post-paren style: func foo() ReturnType  (Go, no arrow)
    m = re.search(r'\)\s+(\*?\w+)\s*$', sig)
    if m:
        token = m.group(1).lstrip('*')
        return token if token and token not in _SKIP_RETURN else None

    return None


def generate_questions(repo: RepoIndex) -> list[BenchQuestion]:
    """Auto-generate factual questions from the repo index."""
    questions: list[BenchQuestion] = []

    for fi in repo.files:

        # Imports question - requires at least 2 imports to be meaningful
        if len(fi.imports) >= 2:
            questions.append(BenchQuestion(
                kind="imports",
                question=f"What modules does `{fi.path}` import?",
                file=fi.path,
                symbol="",
                expected_terms=fi.imports[:5],
            ))

        for sym in _flatten(fi.symbols):

            if sym.kind in ("function", "method"):
                params = _param_names(sym.signature)
                if params:
                    questions.append(BenchQuestion(
                        kind="params",
                        question=f"What parameters does `{sym.name}` accept in `{fi.path}`?",
                        file=fi.path,
                        symbol=sym.name,
                        expected_terms=params,
                    ))

                ret = _return_type(sym.signature)
                if ret:
                    questions.append(BenchQuestion(
                        kind="return_type",
                        question=f"What type does `{sym.name}` return in `{fi.path}`?",
                        file=fi.path,
                        symbol=sym.name,
                        expected_terms=[ret],
                    ))

            elif sym.kind in ("class", "impl", "struct", "trait") and len(sym.children) >= 2:
                child_names = [c.name for c in sym.children]
                questions.append(BenchQuestion(
                    kind="methods",
                    question=f"What methods does `{sym.name}` have in `{fi.path}`?",
                    file=fi.path,
                    symbol=sym.name,
                    expected_terms=child_names,
                ))

    return questions


# ── skeleton search helpers ───────────────────────────────────────────────────

def _symbol_lines(symbol: str, skeleton: str) -> str:
    """Return the full signature for this symbol, including multi-line params.

    Tracks open-paren depth so that multi-line signatures like:
        def index(
            path: str = typer.Argument(...),
        )
    are captured in full.
    """
    lines = skeleton.splitlines()
    collected: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if symbol in line:
            collected.append(line)
            # Follow the signature until parens balance
            depth = line.count('(') - line.count(')')
            j = i + 1
            while depth > 0 and j < len(lines):
                collected.append(lines[j])
                depth += lines[j].count('(') - lines[j].count(')')
                j += 1
            i = j
        else:
            i += 1
    return "\n".join(collected)


# Keywords that introduce a type definition in the skeleton
_DEF_KEYWORDS = ('class ', 'struct ', 'impl ', 'trait ', 'module ', 'interface ', 'enum ')


def _is_type_definition(class_name: str, line: str) -> bool:
    """True when `line` defines (not merely references) class_name."""
    stripped = line.strip()
    for kw in _DEF_KEYWORDS:
        if stripped.startswith(kw + class_name) or stripped.startswith(kw.strip() + '(' + class_name):
            return True
        # Handle modifiers before keyword: "abstract class Foo", "data class Foo"
        if (kw + class_name) in stripped:
            # Verify it's not just a reference (e.g., return type)
            parts = stripped.split()
            if kw.strip() in parts:
                kw_idx = parts.index(kw.strip())
                if kw_idx + 1 < len(parts) and parts[kw_idx + 1] == class_name:
                    return True
    # Bare "ClassName:" pattern (Ruby)
    if stripped == class_name + ':' or stripped.startswith(class_name + '('):
        return True
    return False


def _class_block(class_name: str, skeleton: str) -> str:
    """Return every definition block for class_name (class, struct, impl, trait…).

    Handles Rust's struct Foo + impl Foo split, while ignoring lines that merely
    reference the name as a return type or parameter type.
    """
    lines = skeleton.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith(" ") and _is_type_definition(class_name, line):
            block = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.startswith("  "):
                    block.append(next_line)
                    j += 1
                elif next_line.strip() == "":
                    j += 1
                else:
                    break
            blocks.append("\n".join(block))
            i = j
        else:
            i += 1
    return "\n".join(blocks)


def _imports_line(skeleton: str) -> str:
    for line in skeleton.splitlines():
        if line.startswith("imports:"):
            return line
    return ""


# ── heuristic scoring ─────────────────────────────────────────────────────────

def score_heuristic(questions: list[BenchQuestion], repo: RepoIndex) -> list[QuestionResult]:
    """Check each question by searching the skeleton text for expected terms."""
    skeleton_by_file = {fi.path: format_file_text(fi) for fi in repo.files}
    results: list[QuestionResult] = []

    for q in questions:
        skeleton = skeleton_by_file.get(q.file, "")

        if q.kind in ("params", "return_type"):
            search_text = _symbol_lines(q.symbol, skeleton)
        elif q.kind == "methods":
            search_text = _class_block(q.symbol, skeleton)
        else:
            search_text = _imports_line(skeleton)

        found = [t for t in q.expected_terms if t in search_text]
        missing = [t for t in q.expected_terms if t not in search_text]

        results.append(QuestionResult(
            question=q,
            passed=not missing,
            found_terms=found,
            missing_terms=missing,
        ))

    return results


# ── LLM scoring ───────────────────────────────────────────────────────────────

def _sample_questions(questions: list[BenchQuestion], n: int) -> list[BenchQuestion]:
    """Sample n questions evenly across kinds."""
    by_kind: dict[str, list[BenchQuestion]] = {}
    for q in questions:
        by_kind.setdefault(q.kind, []).append(q)

    per_kind = max(1, n // len(by_kind))
    sampled: list[BenchQuestion] = []
    for qs in by_kind.values():
        sampled.extend(random.sample(qs, min(per_kind, len(qs))))

    # Shuffle and cap at n
    random.shuffle(sampled)
    return sampled[:n]


def _ask_claude(client, question: str, context: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=(
            "You are a code analysis assistant. Answer questions about code "
            "precisely and concisely based only on the provided context. "
            "Keep answers under 3 sentences."
        ),
        messages=[{
            "role": "user",
            "content": f"Code context:\n```\n{context[:8000]}\n```\n\nQuestion: {question}",
        }],
    )
    return resp.content[0].text.strip()


def _judge_equivalence(client, question: str, answer_a: str, answer_b: str) -> tuple[bool, str]:
    """Ask Claude Haiku whether two answers convey equivalent information."""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{
            "role": "user",
            "content": (
                f"A developer asked: {question}\n\n"
                f"Answer A (from full source code):\n{answer_a}\n\n"
                f"Answer B (from compressed skeleton):\n{answer_b}\n\n"
                "Judge only factual technical accuracy about code structure. "
                "Ignore differences in verbosity, phrasing, confidence hedges, or extra context. "
                "Are the core technical facts in both answers the same?\n"
                "Reply with exactly EQUIVALENT or NOT_EQUIVALENT on the first line, "
                "then one sentence explaining why."
            ),
        }],
    )
    text = resp.content[0].text.strip()
    first_line = text.splitlines()[0].upper()
    equivalent = "NOT_EQUIVALENT" not in first_line and "EQUIVALENT" in first_line
    reasoning = text.split('\n', 1)[-1].strip() if '\n' in text else ""
    return equivalent, reasoning


def score_llm(
    questions: list[BenchQuestion],
    repo: RepoIndex,
    repo_path: str,
    sample_size: int,
) -> list[LLMQuestionResult]:
    """Run LLM semantic equivalence eval using Claude."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package required for --llm mode.\n"
            "Install it with: pip install 'reducethemtokens[llm]'"
        )

    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set.\n"
            "Export it before running: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    client = anthropic.Anthropic(api_key=api_key)
    sampled = _sample_questions(questions, sample_size)

    root = Path(repo_path).resolve()
    source_map = {
        fi.path: (root / fi.path).read_text(errors="replace")
        for fi in repo.files
        if (root / fi.path).exists()
    }
    skeleton_map = {fi.path: format_file_text(fi) for fi in repo.files}

    results: list[LLMQuestionResult] = []
    for q in sampled:
        full_ctx = source_map.get(q.file, "")
        skel_ctx = skeleton_map.get(q.file, "")

        full_answer = _ask_claude(client, q.question, full_ctx)
        skeleton_answer = _ask_claude(client, q.question, skel_ctx)
        equivalent, reasoning = _judge_equivalence(client, q.question, full_answer, skeleton_answer)

        results.append(LLMQuestionResult(
            question=q.question,
            full_answer=full_answer,
            skeleton_answer=skeleton_answer,
            equivalent=equivalent,
            reasoning=reasoning,
        ))

    return results


# ── top-level entry point ─────────────────────────────────────────────────────

def run_bench(
    path: str,
    use_llm: bool = False,
    llm_sample: int = 20,
) -> BenchReport:
    root = Path(path).resolve()
    repo = extract_repo(str(root), use_cache=False)
    questions = generate_questions(repo)

    heuristic = score_heuristic(questions, repo)
    llm: list[LLMQuestionResult] = []

    if use_llm:
        llm = score_llm(questions, repo, str(root), llm_sample)

    return BenchReport(
        path=str(root),
        total_questions=len(questions),
        heuristic_results=heuristic,
        llm_results=llm,
    )
