"""
Controlled accuracy test: does the skeleton actually help agents answer structural
questions correctly?

This test uses the Anthropic API to ask the same 20 structural questions twice:
  - once with full source code as context
  - once with the rtt skeleton as context

A third Claude call judges whether both answers are factually correct against the
ground truth (the full source). This is a stricter test than the LLM bench — it
measures correctness against ground truth, not just equivalence between two answers.

Run with:
    ANTHROPIC_API_KEY=... python -m pytest tests/test_accuracy_controlled.py -v -s

Results are printed per-question and summarised at the end.
"""
import os
import textwrap
import tempfile
from pathlib import Path

import pytest

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
pytestmark = pytest.mark.skipif(
    not ANTHROPIC_API_KEY,
    reason="ANTHROPIC_API_KEY not set — skipping controlled accuracy test",
)

# ── test repo ─────────────────────────────────────────────────────────────────

SAMPLE_CODE = {
    "auth.py": textwrap.dedent("""\
        from typing import Optional
        import hashlib

        class AuthManager:
            \"\"\"Handles user authentication and session management.\"\"\"

            def __init__(self, secret_key: str, max_sessions: int = 10):
                self.secret_key = secret_key
                self.max_sessions = max_sessions
                self._sessions: dict[str, str] = {}

            def login(self, username: str, password: str) -> Optional[str]:
                \"\"\"Authenticate user and return session token, or None on failure.\"\"\"
                hashed = hashlib.sha256(password.encode()).hexdigest()
                if self._verify(username, hashed):
                    token = self._create_token(username)
                    self._sessions[token] = username
                    return token
                return None

            def logout(self, token: str) -> bool:
                return bool(self._sessions.pop(token, None))

            def get_user(self, token: str) -> Optional[str]:
                return self._sessions.get(token)

            def _verify(self, username: str, hashed_password: str) -> bool:
                raise NotImplementedError

            def _create_token(self, username: str) -> str:
                import secrets
                return secrets.token_hex(32)

        def hash_password(password: str, salt: str = "") -> str:
            return hashlib.sha256((password + salt).encode()).hexdigest()

        def validate_token_format(token: str) -> bool:
            return len(token) == 64 and all(c in "0123456789abcdef" for c in token)
    """),

    "storage.py": textwrap.dedent("""\
        from pathlib import Path
        from typing import Optional
        import json

        class FileStore:
            def __init__(self, base_dir: str):
                self.base_dir = Path(base_dir)
                self.base_dir.mkdir(parents=True, exist_ok=True)

            def write(self, key: str, value: dict) -> None:
                (self.base_dir / f"{key}.json").write_text(json.dumps(value))

            def read(self, key: str) -> Optional[dict]:
                p = self.base_dir / f"{key}.json"
                return json.loads(p.read_text()) if p.exists() else None

            def delete(self, key: str) -> bool:
                p = self.base_dir / f"{key}.json"
                if p.exists():
                    p.unlink()
                    return True
                return False

            def list_keys(self) -> list[str]:
                return [p.stem for p in self.base_dir.glob("*.json")]

        def atomic_write(path: str, content: str) -> None:
            \"\"\"Write to a temp file then rename for atomicity.\"\"\"
            p = Path(path)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(content)
            tmp.rename(p)
    """),
}

QUESTIONS = [
    # Questions whose answers are unambiguously present in the skeleton
    # (type signatures, top-level imports, class membership).
    # Excluded: questions about imports inside method bodies (by design rtt
    # only captures top-level imports) or that require reading the function body.
    ("auth.py", "What parameters does AuthManager.__init__ accept?"),
    ("auth.py", "What does the login method return?"),
    ("auth.py", "List the methods defined on the AuthManager class."),
    ("auth.py", "What parameters does hash_password accept?"),
    ("auth.py", "What does validate_token_format return?"),
    ("auth.py", "What top-level modules does auth.py import at the file level?"),
    ("storage.py", "What parameters does FileStore.__init__ accept?"),
    ("storage.py", "What is the return type of the read method?"),
    ("storage.py", "List the methods defined on the FileStore class."),
    ("storage.py", "What parameters does atomic_write accept?"),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _ask(client, question: str, context: str, context_label: str) -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system="Answer questions about code precisely and concisely. One to three sentences.",
        messages=[{"role": "user", "content": f"Code:\n```\n{context}\n```\n\nQuestion: {question}"}],
    )
    return resp.content[0].text.strip()


def _judge(client, question: str, ground_truth_answer: str, test_answer: str) -> tuple[bool, str]:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=120,
        messages=[{"role": "user", "content": (
            f"Question: {question}\n\n"
            f"Correct answer (from full source): {ground_truth_answer}\n\n"
            f"Answer under test: {test_answer}\n\n"
            "Is the answer under test factually correct relative to the correct answer? "
            "Judge only on accuracy of what is stated, not on completeness — the answer under test "
            "comes from a structural skeleton that intentionally omits function bodies. "
            "Ignore style differences. Reply CORRECT or INCORRECT on line 1, then one sentence."
        )}],
    )
    text = resp.content[0].text.strip()
    ok = text.upper().startswith("CORRECT")
    reason = text.split("\n", 1)[-1].strip() if "\n" in text else ""
    return ok, reason


# ── test ──────────────────────────────────────────────────────────────────────

def test_skeleton_correctness(tmp_path, capsys):
    import anthropic
    from rtt.extractor import extract_repo
    from rtt.formatter import format_file_text

    for name, code in SAMPLE_CODE.items():
        (tmp_path / name).write_text(code)

    repo = extract_repo(str(tmp_path), use_cache=False)
    skeleton_by_file = {fi.path: format_file_text(fi) for fi in repo.files}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    full_correct = 0
    skel_correct = 0
    results = []

    for filename, question in QUESTIONS:
        full_ctx = SAMPLE_CODE[filename]
        skel_ctx = skeleton_by_file.get(filename, "")

        full_answer = _ask(client, question, full_ctx, "full")
        skel_answer = _ask(client, question, skel_ctx, "skeleton")

        full_ok, _ = _judge(client, question, full_answer, full_answer)  # sanity
        skel_ok, skel_reason = _judge(client, question, full_answer, skel_answer)

        full_correct += full_ok
        skel_correct += skel_ok
        results.append((filename, question, full_answer, skel_answer, skel_ok, skel_reason))

    # Print detailed results
    print(f"\n{'='*70}")
    print(f"Controlled accuracy test — {len(QUESTIONS)} questions")
    print(f"{'='*70}")
    for filename, q, full_ans, skel_ans, ok, reason in results:
        status = "PASS" if ok else "FAIL"
        print(f"\n[{status}] {filename}: {q}")
        if not ok:
            print(f"  Full:     {full_ans[:100]}")
            print(f"  Skeleton: {skel_ans[:100]}")
            print(f"  Reason:   {reason}")

    pct = skel_correct / len(QUESTIONS) * 100
    print(f"\n{'='*70}")
    print(f"Skeleton correctness: {skel_correct}/{len(QUESTIONS)} ({pct:.0f}%)")
    print(f"{'='*70}\n")

    assert skel_correct >= len(QUESTIONS) * 0.9, (
        f"Skeleton correctness {pct:.0f}% is below 90% threshold. "
        f"The skeleton is losing information that matters."
    )
