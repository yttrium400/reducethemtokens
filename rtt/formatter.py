from __future__ import annotations
import datetime
import json
from dataclasses import asdict
from rtt import RepoIndex, FileIndex, Symbol


def format_text(repo: RepoIndex) -> str:
    """Compact plain-text skeleton - optimized for token efficiency."""
    parts = []
    for file_index in repo.files:
        text = format_file_text(file_index)
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def format_json(repo: RepoIndex) -> str:
    """Serialize the repo index as structured JSON."""
    data = asdict(repo)
    return json.dumps(data, indent=2, ensure_ascii=False)


def format_text_with_header(repo: RepoIndex, token_count: int) -> str:
    """Skeleton with a staleness header as the first line.

    The header gives agents a signal for how fresh the index is:
        # rtt index | generated 2025-05-14 13:00 | 3020 files | 585421 tokens
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n_files = len(repo.files)
    dropped = getattr(repo, "_dropped", 0)
    drop_note = f" | {dropped} files excluded by budget" if dropped else ""
    header = (
        f"# rtt index | generated {now} | {n_files} files | "
        f"{token_count} tokens{drop_note}\n"
        f"# If this looks outdated, ask the user to run: rtt update\n"
    )
    return header + format_text(repo)


def format_file_text(file_index: FileIndex) -> str:
    lines = []
    lines.append(f"# {file_index.path} [{file_index.language}]")

    if file_index.imports:
        lines.append(f"imports: {', '.join(file_index.imports)}")

    for sym in file_index.symbols:
        lines.extend(_format_symbol_text(sym, indent=0))

    lines.append("")
    return "\n".join(lines)


def _format_symbol_text(sym: Symbol, indent: int) -> list[str]:
    prefix = "  " * indent
    lines = []

    line = f"{prefix}{sym.signature}"
    if sym.docstring:
        line += f"  # {sym.docstring}"
    lines.append(line)

    for child in sym.children:
        lines.extend(_format_symbol_text(child, indent + 1))

    return lines


def format_markdown(repo: RepoIndex) -> str:
    """Human-readable markdown view."""
    parts = ["# Repository Index\n"]
    for file_index in repo.files:
        parts.append(format_file_markdown(file_index))
    return "\n".join(parts)


def format_file_markdown(file_index: FileIndex) -> str:
    lines = []
    lines.append(f"## `{file_index.path}` · {file_index.language}")

    if file_index.imports:
        lines.append(f"\n**Imports:** {', '.join(f'`{i}`' for i in file_index.imports)}\n")

    for sym in file_index.symbols:
        lines.extend(_format_symbol_markdown(sym, level=3))

    lines.append("")
    return "\n".join(lines)


def _format_symbol_markdown(sym: Symbol, level: int) -> list[str]:
    hashes = "#" * min(level, 6)
    lines = []

    kind_label = sym.kind.capitalize()
    lines.append(f"\n{hashes} `{sym.name}` ({kind_label})")
    lines.append(f"```\n{sym.signature}\n```")

    if sym.docstring:
        lines.append(f"_{sym.docstring}_")

    if sym.children:
        lines.append("")
        for child in sym.children:
            lines.extend(_format_symbol_markdown(child, level + 1))

    return lines
