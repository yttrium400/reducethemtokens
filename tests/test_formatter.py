import json
from pathlib import Path

from rtt import RepoIndex, FileIndex, Symbol
from rtt.formatter import format_json


def _make_repo() -> RepoIndex:
    return RepoIndex(files=[
        FileIndex(
            path="src/auth.py",
            language="python",
            imports=["os", "hashlib"],
            symbols=[
                Symbol(
                    name="hash_password",
                    kind="function",
                    signature="def hash_password(password: str) -> str",
                    docstring="Hash a password with SHA-256.",
                    children=[],
                ),
                Symbol(
                    name="AuthManager",
                    kind="class",
                    signature="class AuthManager",
                    children=[
                        Symbol(
                            name="login",
                            kind="method",
                            signature="def login(self, user: str, pwd: str) -> bool",
                        ),
                    ],
                ),
            ],
        ),
        FileIndex(
            path="utils/helper.ts",
            language="typescript",
            imports=["lodash", "fs"],
            symbols=[
                Symbol(
                    name="formatDate",
                    kind="function",
                    signature="function formatDate(d: Date): string",
                ),
            ],
        ),
    ])


def test_format_json_is_valid_json():
    repo = _make_repo()
    output = format_json(repo)
    data = json.loads(output)
    assert isinstance(data, dict)
    assert "files" in data


def test_format_json_file_count():
    repo = _make_repo()
    data = json.loads(format_json(repo))
    assert len(data["files"]) == 2


def test_format_json_file_fields():
    repo = _make_repo()
    data = json.loads(format_json(repo))
    f = data["files"][0]
    assert f["path"] == "src/auth.py"
    assert f["language"] == "python"
    assert f["imports"] == ["os", "hashlib"]


def test_format_json_symbols():
    repo = _make_repo()
    data = json.loads(format_json(repo))
    syms = data["files"][0]["symbols"]
    assert len(syms) == 2
    assert syms[0]["name"] == "hash_password"
    assert syms[0]["kind"] == "function"
    assert "password" in syms[0]["signature"]


def test_format_json_nested_children():
    repo = _make_repo()
    data = json.loads(format_json(repo))
    auth_class = data["files"][0]["symbols"][1]
    assert auth_class["name"] == "AuthManager"
    assert len(auth_class["children"]) == 1
    assert auth_class["children"][0]["name"] == "login"


def test_format_json_empty_repo():
    repo = RepoIndex(files=[])
    data = json.loads(format_json(repo))
    assert data == {"files": []}
