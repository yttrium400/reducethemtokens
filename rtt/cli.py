import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

app = typer.Typer(
    name="rtt",
    help="Compress any code repo into a compact skeleton to reduce LLM token usage.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _resolve_path(path: str) -> str:
    p = Path(path).resolve()
    if not p.exists():
        err_console.print(f"[red]Error:[/red] Path not found: {path}")
        raise typer.Exit(1)
    return str(p)


@app.command()
def index(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable file cache"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write output to file"),
    include: Optional[list[str]] = typer.Option(None, "--include", "-i",
        help="Glob pattern to include (repeatable). e.g. --include 'src/**' --include '*.py'"),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", "-e",
        help="Glob pattern to exclude (repeatable). e.g. --exclude 'tests/**'"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", "-m",
        help="Trim output to fit within this token budget, dropping low-priority files first."),
    no_tests: bool = typer.Option(False, "--no-tests",
        help="Exclude test/spec/fixture files."),
    format: str = typer.Option("text", "--format", "-f",
        help="Output format: text (default) or json."),
):
    """Generate a compact skeleton index of the repo and print to stdout."""
    from rtt.extractor import extract_repo
    from rtt.formatter import format_json

    if format not in ("text", "json"):
        err_console.print(f"[red]Error:[/red] Unknown format '{format}'. Valid: text, json")
        raise typer.Exit(1)

    resolved = _resolve_path(path)

    with console.status("[dim]Indexing...[/dim]", spinner="dots"):
        repo = extract_repo(resolved, use_cache=not no_cache,
                            include=include, exclude=exclude, max_tokens=max_tokens,
                            no_tests=no_tests)

    if format == "json":
        text = format_json(repo)
    else:
        text = repo.text

    tokens = repo.token_count
    dropped = getattr(repo, "_dropped", 0)

    if output:
        Path(output).write_text(text)
        msg = f"[green]Written to {output}[/green] ({tokens:,} tokens, {len(repo.files)} files"
        if dropped:
            msg += f", [yellow]{dropped} files excluded by --max-tokens[/yellow]"
        console.print(msg + ")")
    else:
        print(text)
        if dropped:
            err_console.print(f"[yellow]{dropped} files excluded to stay within --max-tokens budget[/yellow]")


@app.command()
def compare(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    diff: Optional[str] = typer.Option(None, "--diff", help="Git range, e.g. HEAD~1..HEAD"),
    top: int = typer.Option(10, "--top", "-n", help="Number of top files to show"),
):
    """Show token reduction stats before and after compression."""
    resolved = _resolve_path(path)

    if diff:
        _compare_git(resolved, diff, top)
    else:
        _compare_snapshot(resolved, top)


def _compare_snapshot(path: str, top: int):
    from rtt.extractor import compare_repo

    with console.status("[dim]Analyzing...[/dim]", spinner="dots"):
        report = compare_repo(path)

    console.print()
    console.print("[bold]Token Usage[/bold]")
    console.print("─" * 50)
    console.print(f"  Raw codebase:    [red]{report.raw_tokens:>12,}[/red] tokens  ({report.file_count} files)")
    console.print(f"  With rtt:        [green]{report.compressed_tokens:>12,}[/green] tokens")
    console.print(f"  Reduction:       [bold green]{report.reduction_pct:>11.1f}%[/bold green]  (-{report.raw_tokens - report.compressed_tokens:,} tokens)")
    console.print()

    table = Table(title=f"Top {top} files by raw token count", show_header=True, header_style="bold")
    table.add_column("File", style="dim", no_wrap=False)
    table.add_column("Raw", justify="right")
    table.add_column("Compressed", justify="right")
    table.add_column("Reduction", justify="right")

    for entry in report.per_file[:top]:
        raw = entry["raw"]
        compressed = entry["compressed"]
        pct = (1 - compressed / raw) * 100 if raw > 0 else 0
        table.add_row(
            entry["path"],
            f"{raw:,}",
            f"[green]{compressed:,}[/green]",
            f"[green]{pct:.0f}%[/green]",
        )

    console.print(table)


def _compare_git(path: str, diff_range: str, top: int):
    import subprocess
    import tempfile
    from rtt.extractor import extract_repo
    from rtt.tokenizer import count_tokens
    from rtt.formatter import format_text

    # Get list of changed files
    result = subprocess.run(
        ["git", "-C", path, "diff", "--name-only", diff_range],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        err_console.print(f"[red]git diff failed:[/red] {result.stderr.strip()}")
        raise typer.Exit(1)

    changed_files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]

    if not changed_files:
        console.print("[yellow]No files changed in range.[/yellow]")
        return

    # Get commits from range
    parts = diff_range.split("..")
    before_ref = parts[0] if len(parts) == 2 else f"{diff_range}^"
    after_ref = parts[1] if len(parts) == 2 else diff_range

    def index_at_ref(ref: str) -> int:
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                ["git", "-C", path, "worktree", "add", "--detach", tmpdir, ref],
                capture_output=True,
            )
            try:
                repo = extract_repo(tmpdir, use_cache=False)
                return count_tokens(format_text(repo))
            finally:
                subprocess.run(
                    ["git", "-C", path, "worktree", "remove", "--force", tmpdir],
                    capture_output=True,
                )

    with console.status("[dim]Indexing before state...[/dim]", spinner="dots"):
        before_tokens = index_at_ref(before_ref)

    with console.status("[dim]Indexing after state...[/dim]", spinner="dots"):
        after_tokens = index_at_ref(after_ref)

    delta = after_tokens - before_tokens
    sign = "+" if delta >= 0 else ""
    color = "red" if delta > 0 else "green"

    console.print()
    console.print(f"[bold]Commit delta[/bold]  [dim]{diff_range}[/dim]")
    console.print("─" * 50)
    console.print(f"  Before: [dim]{before_tokens:>10,}[/dim] tokens")
    console.print(f"  After:  [dim]{after_tokens:>10,}[/dim] tokens")
    console.print(f"  Delta:  [{color}]{sign}{delta:,} tokens ({sign}{delta/before_tokens*100:.1f}%)[/{color}]")
    console.print()
    console.print(f"[dim]Changed files ({len(changed_files)}):[/dim]")
    for f in changed_files:
        console.print(f"  [dim]{f}[/dim]")


@app.command()
def bench(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    llm: bool = typer.Option(False, "--llm", help="Also run LLM semantic equivalence eval (requires ANTHROPIC_API_KEY)"),
    sample: int = typer.Option(20, "--sample", "-n", help="Questions to send to LLM (--llm only)"),
    show_failing: bool = typer.Option(False, "--show-failing", help="Print details of every failing question"),
):
    """Benchmark how much information the skeleton retains vs full source.

    Default mode (free, instant): checks that every expected fact - parameter
    names, return types, method lists, imports - is present in the skeleton.

    --llm mode: sends sampled questions to Claude twice (full source vs skeleton)
    and uses Claude-as-judge to measure semantic equivalence.
    """
    from rtt.bench import run_bench

    resolved = _resolve_path(path)

    label = "Running benchmark" + (" + LLM eval" if llm else "")
    with console.status(f"[dim]{label}...[/dim]", spinner="dots"):
        try:
            report = run_bench(resolved, use_llm=llm, llm_sample=sample)
        except RuntimeError as e:
            err_console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # ── heuristic results ─────────────────────────────────────────────────────
    console.print()
    h_color = "green" if report.heuristic_score >= 98 else "yellow" if report.heuristic_score >= 90 else "red"
    console.print("[bold]Information Retention Benchmark[/bold]")
    console.print("─" * 54)
    console.print(f"  Questions generated:  {report.total_questions:>6,}")
    console.print(
        f"  Score:               [{h_color}]{report.heuristic_score:>5.1f}%[/{h_color}]"
        f"  ({sum(r.passed for r in report.heuristic_results)}/{len(report.heuristic_results)})"
    )
    console.print()

    # Per-kind breakdown
    by_kind = report.heuristic_by_kind
    if by_kind:
        table = Table(title="By question type", show_header=True, header_style="bold", box=None)
        table.add_column("Kind", style="dim")
        table.add_column("Passed / Total", justify="right")
        table.add_column("Score", justify="right")

        for kind in ("params", "return_type", "methods", "imports"):
            if kind not in by_kind:
                continue
            passed, total = by_kind[kind]
            score = passed / total * 100
            color = "green" if score >= 98 else "yellow" if score >= 90 else "red"
            table.add_row(kind, f"{passed} / {total}", f"[{color}]{score:.1f}%[/{color}]")

        console.print(table)
        console.print()

    # Failing questions
    failing = report.heuristic_failing
    if not failing:
        console.print("[bold green]✓ All information retained in skeleton.[/bold green]")
    else:
        console.print(f"[bold yellow]Failing questions ({len(failing)})[/bold yellow]")
        console.print("─" * 54)
        shown = failing if show_failing else failing[:10]
        for r in shown:
            console.print(
                f"  [dim]{r.question.file}[/dim] :: [yellow]{r.question.symbol or 'imports'}[/yellow]"
                f"  [dim]({r.question.kind})[/dim]"
            )
            console.print(f"    missing: [red]{', '.join(r.missing_terms)}[/red]")
        if not show_failing and len(failing) > 10:
            console.print(f"  [dim]... and {len(failing) - 10} more. Use --show-failing to see all.[/dim]")

    # ── LLM results ───────────────────────────────────────────────────────────
    if report.llm_results:
        llm_score = report.llm_score
        l_color = "green" if llm_score >= 90 else "yellow" if llm_score >= 75 else "red"
        console.print()
        console.print("[bold]LLM Semantic Equivalence[/bold]  [dim](Claude-as-judge)[/dim]")
        console.print("─" * 54)
        console.print(
            f"  Questions sampled:   {len(report.llm_results):>6}\n"
            f"  Equivalent answers:  [{l_color}]{llm_score:>5.1f}%[/{l_color}]"
            f"  ({sum(r.equivalent for r in report.llm_results)}/{len(report.llm_results)})"
        )

        llm_failing = report.llm_failing
        if llm_failing:
            console.print()
            console.print(f"[bold yellow]Non-equivalent answers ({len(llm_failing)})[/bold yellow]")
            shown = llm_failing if show_failing else llm_failing[:5]
            for r in shown:
                console.print(f"\n  [yellow]Q:[/yellow] {r.question}")
                console.print(f"  [dim]Full:     {r.full_answer[:120]}[/dim]")
                console.print(f"  [dim]Skeleton: {r.skeleton_answer[:120]}[/dim]")
                if r.reasoning:
                    console.print(f"  [dim]Reason:   {r.reasoning}[/dim]")
        else:
            console.print("[bold green]✓ All sampled answers were semantically equivalent.[/bold green]")


@app.command()
def audit(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    show_passing: bool = typer.Option(False, "--show-passing", help="Also list files with no issues"),
    top: int = typer.Option(0, "--top", "-n", help="Limit to N files with issues (0 = all)"),
):
    """Audit extraction accuracy: coverage (symbols found vs expected) and signature correctness."""
    from rtt.audit import audit_repo

    resolved = _resolve_path(path)

    with console.status("[dim]Auditing...[/dim]", spinner="dots"):
        report = audit_repo(resolved)

    if not report.files:
        console.print("[yellow]No supported files found.[/yellow]")
        return

    # Summary header
    console.print()
    coverage_color = "green" if report.coverage >= 95 else "yellow" if report.coverage >= 80 else "red"
    sig_color = "green" if report.total_signature_issues == 0 else "yellow" if report.total_signature_issues <= 3 else "red"

    console.print("[bold]Audit Summary[/bold]")
    console.print("─" * 54)
    console.print(f"  Files audited:       {len(report.files):>6}")
    console.print(f"  Symbols expected:    {report.total_expected:>6,}")
    console.print(f"  Symbols found:       {report.total_found:>6,}")
    console.print(f"  Coverage:            [{coverage_color}]{report.coverage:>5.1f}%[/{coverage_color}]")
    console.print(f"  Signature issues:    [{sig_color}]{report.total_signature_issues:>6}[/{sig_color}]")
    console.print()

    # Per-language breakdown
    by_lang: dict[str, dict] = {}
    for f in report.files:
        lang = f.language
        if lang not in by_lang:
            by_lang[lang] = {"expected": 0, "found": 0, "sig_issues": 0}
        by_lang[lang]["expected"] += f.expected
        by_lang[lang]["found"] += f.found
        by_lang[lang]["sig_issues"] += len(f.signature_issues)

    if len(by_lang) > 1:
        table = Table(title="Coverage by language", show_header=True, header_style="bold", box=None)
        table.add_column("Language", style="dim")
        table.add_column("Found / Expected", justify="right")
        table.add_column("Coverage", justify="right")
        table.add_column("Sig issues", justify="right")

        for lang, stats in sorted(by_lang.items()):
            cov = stats["found"] / stats["expected"] * 100 if stats["expected"] else 100.0
            color = "green" if cov >= 95 else "yellow" if cov >= 80 else "red"
            flag = "  [yellow]←[/yellow]" if cov < 95 or stats["sig_issues"] > 0 else ""
            table.add_row(
                lang,
                f"{stats['found']} / {stats['expected']}",
                f"[{color}]{cov:.1f}%[/{color}]{flag}",
                str(stats["sig_issues"]) if stats["sig_issues"] else "[dim]0[/dim]",
            )

        console.print(table)
        console.print()

    # Files with issues
    problem_files = report.files_with_issues
    if top:
        problem_files = problem_files[:top]

    if not problem_files:
        console.print("[bold green]✓ No issues found.[/bold green]")
    else:
        console.print(f"[bold]Files with issues ({len(report.files_with_issues)})[/bold]")
        console.print("─" * 54)

        for fa in problem_files:
            cov_color = "green" if fa.coverage >= 95 else "yellow" if fa.coverage >= 80 else "red"
            console.print(
                f"  [dim]{fa.path}[/dim]  "
                f"[{cov_color}]{fa.coverage:.0f}% coverage[/{cov_color}]"
                + (f"  [yellow]{len(fa.signature_issues)} sig issue(s)[/yellow]" if fa.signature_issues else "")
            )

            for gt in fa.missing:
                console.print(f"    [red]✗ missing:[/red] {gt.name}  [dim]({gt.kind}, line {gt.line})[/dim]")

            for issue in fa.signature_issues:
                console.print(f"    [yellow]⚠ signature:[/yellow] {issue.symbol_name}  [dim]- {issue.issue}[/dim]")
                console.print(f"      [dim]got: {issue.signature[:80]}[/dim]")

        console.print()

    if show_passing:
        passing = [f for f in report.files if f.passed]
        if passing:
            console.print(f"[dim]Passing files ({len(passing)}):[/dim]")
            for f in passing:
                console.print(f"  [dim green]✓ {f.path}[/dim green]  [dim]{f.found}/{f.expected}[/dim]")


@app.command()
def install(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    platform: Optional[str] = typer.Option(None, "--platform", "-p",
        help="Target platform: claude, cursor, windsurf, codex, copilot, kiro, gemini, aider, zed. Default: auto-detect."),
    auto_detect: bool = typer.Option(True, "--auto-detect/--all",
        help="Auto-detect installed platforms (default) or install for all."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing rtt sections"),
    include: Optional[list[str]] = typer.Option(None, "--include", "-i",
        help="Glob pattern to include (repeatable)."),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", "-e",
        help="Glob pattern to exclude (repeatable)."),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", "-m",
        help="Trim skeleton to fit within this token budget."),
    no_tests: bool = typer.Option(False, "--no-tests",
        help="Exclude test/spec/fixture files from the skeleton."),
):
    """Index the repo and inject context instructions into agent config files.

    Writes the skeleton to .rtt/context.txt, then adds a section to each
    agent's config file instructing it to read that file at session start -
    before opening any source files.

    By default, auto-detects which AI agents are in use by checking for
    platform-specific config files/dirs. Use --all to install for every
    supported platform regardless.

    Supports: Claude Code (CLAUDE.md), Cursor (.cursor/rules/), Windsurf
    (.windsurfrules), Codex/OpenAI (AGENTS.md), GitHub Copilot
    (.github/copilot-instructions.md), Kiro, Gemini CLI, Aider, Zed.
    """
    from rtt.extractor import extract_repo, compare_repo
    from rtt.formatter import format_text
    from rtt.tokenizer import count_tokens
    from rtt.installer import install as do_install, detect_platforms, PLATFORMS, PLATFORM_BY_NAME

    resolved = _resolve_path(path)

    # Validate platform name early
    if platform and platform not in PLATFORM_BY_NAME:
        valid = ", ".join(p.name for p in PLATFORMS)
        err_console.print(f"[red]Error:[/red] Unknown platform '{platform}'. Valid: {valid}")
        raise typer.Exit(1)

    # Determine target platforms
    if platform:
        platform_names = [platform]
    elif auto_detect:
        detected = detect_platforms(resolved)
        if detected:
            console.print(f"[dim]Detected:[/dim] {', '.join(detected)}")
            platform_names = detected
        else:
            console.print("[dim]No platforms detected, installing for all.[/dim]")
            platform_names = None
    else:
        platform_names = None

    with console.status("[dim]Indexing repo...[/dim]", spinner="dots"):
        repo       = extract_repo(resolved, use_cache=False,
                                  include=include, exclude=exclude, max_tokens=max_tokens,
                                  no_tests=no_tests)
        text       = format_text(repo)
        compressed = count_tokens(text)

    with console.status("[dim]Counting raw tokens...[/dim]", spinner="dots"):
        report    = compare_repo(resolved)
        raw       = report.raw_tokens
        reduction = report.reduction_pct

    # Write skeleton file with staleness header
    from rtt.formatter import format_text_with_header
    skel_dir  = Path(resolved) / ".rtt"
    skel_file = skel_dir / "context.txt"
    skel_dir.mkdir(exist_ok=True)
    skel_file.write_text(format_text_with_header(repo, compressed), encoding="utf-8")
    dropped = getattr(repo, "_dropped", 0)
    drop_note = f"  [yellow]({dropped} files excluded by --max-tokens)[/yellow]" if dropped else ""
    console.print(f"[green]Skeleton written:[/green] .rtt/context.txt  ({compressed:,} tokens){drop_note}")
    if compressed > 100_000 and not no_tests and not max_tokens:
        console.print(
            f"  [dim]Tip: skeleton is large. Try [bold]--no-tests[/bold] to exclude test files, "
            f"or [bold]--max-tokens 100000[/bold] to cap the size.[/dim]"
        )

    # Inject into agent configs
    results = do_install(resolved, platform_names, compressed, raw, reduction, force=force)

    console.print()
    for r in results:
        if r.action == "created":
            console.print(f"  [green]created[/green]  {r.config_file}")
        elif r.action == "updated":
            console.print(f"  [green]updated[/green]  {r.config_file}")
        else:
            console.print(f"  [dim]skipped[/dim]  {r.config_file}  [dim]({r.note})[/dim]")

    # Install git pre-commit hook
    from rtt.installer import install_git_hook
    hook_action = install_git_hook(resolved)
    if hook_action == "created":
        console.print(f"  [green]created[/green]  .git/hooks/pre-commit  [dim](auto-update on commit)[/dim]")
    elif hook_action == "updated":
        console.print(f"  [green]updated[/green]  .git/hooks/pre-commit  [dim](auto-update on commit)[/dim]")

    installed = [r for r in results if r.action != "skipped"]
    console.print()
    if installed or hook_action in ("created", "updated"):
        console.print(
            f"[bold green]Installed to {len(installed)} config file(s).[/bold green] "
            f"Agents will read .rtt/context.txt at session start."
        )
        if hook_action != "skipped":
            console.print(f"[dim].rtt/context.txt will auto-update on every git commit.[/dim]")
    else:
        console.print("[yellow]Nothing changed.[/yellow] Use --force to overwrite existing sections.")


@app.command()
def update(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    diff: bool = typer.Option(False, "--diff", help="Show what changed since last update"),
    include: Optional[list[str]] = typer.Option(None, "--include", "-i",
        help="Glob pattern to include (repeatable)."),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", "-e",
        help="Glob pattern to exclude (repeatable)."),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", "-m",
        help="Trim skeleton to fit within this token budget."),
    no_tests: bool = typer.Option(False, "--no-tests",
        help="Exclude test/spec/fixture files from the skeleton."),
):
    """Regenerate .rtt/context.txt after code changes.

    Re-indexes the repo and overwrites the skeleton file. Agent config files
    are not touched - run this whenever the codebase changes.
    """
    from rtt.extractor import extract_repo
    from rtt.formatter import format_text, format_text_with_header
    from rtt.tokenizer import count_tokens

    resolved = _resolve_path(path)
    skel_file = Path(resolved) / ".rtt" / "context.txt"

    if not skel_file.exists():
        err_console.print(
            "[yellow]Warning:[/yellow] .rtt/context.txt not found. "
            "Run [bold]rtt install[/bold] first to set up agent configs."
        )

    # Snapshot old symbols if diff requested
    old_symbols: set[str] = set()
    if diff and skel_file.exists():
        for line in skel_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("imports:"):
                # Extract the symbol name (first word-like token before ( or space)
                import re as _re
                m = _re.match(r'(?:def |func |function |class |struct |trait |impl |const |var )?(\w+)', stripped)
                if m:
                    old_symbols.add(m.group(1))

    with console.status("[dim]Indexing...[/dim]", spinner="dots"):
        repo   = extract_repo(resolved, use_cache=False,
                              include=include, exclude=exclude, max_tokens=max_tokens,
                              no_tests=no_tests)
        text   = format_text(repo)
        tokens = count_tokens(text)

    skel_file.parent.mkdir(exist_ok=True)
    skel_file.write_text(format_text_with_header(repo, tokens), encoding="utf-8")

    dropped = getattr(repo, "_dropped", 0)
    drop_note = f"  [yellow]({dropped} files excluded)[/yellow]" if dropped else ""
    console.print(f"[green]Updated:[/green] .rtt/context.txt  ({tokens:,} tokens, {len(repo.files)} files){drop_note}")
    if tokens > 100_000 and not no_tests and not max_tokens:
        console.print(
            f"  [dim]Tip: skeleton is large. Try [bold]--no-tests[/bold] to exclude test files, "
            f"or [bold]--max-tokens 100000[/bold] to cap the size.[/dim]"
        )

    if diff:
        new_symbols: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("imports:"):
                import re as _re
                m = _re.match(r'(?:def |func |function |class |struct |trait |impl |const |var )?(\w+)', stripped)
                if m:
                    new_symbols.add(m.group(1))

        added   = new_symbols - old_symbols
        removed = old_symbols - new_symbols

        if not added and not removed:
            console.print("[dim]No structural changes detected.[/dim]")
        else:
            if added:
                console.print(f"  [green]+{len(added)} symbol(s) added:[/green]   {', '.join(sorted(added)[:10])}" +
                              (f" ... and {len(added)-10} more" if len(added) > 10 else ""))
            if removed:
                console.print(f"  [red]-{len(removed)} symbol(s) removed:[/red] {', '.join(sorted(removed)[:10])}" +
                              (f" ... and {len(removed)-10} more" if len(removed) > 10 else ""))


@app.command()
def uninstall(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    platform: Optional[str] = typer.Option(None, "--platform", "-p",
        help="Remove from a specific platform only. Default: all."),
    clean: bool = typer.Option(False, "--clean", help="Also delete .rtt/context.txt"),
):
    """Remove rtt instructions from agent config files."""
    from rtt.installer import uninstall as do_uninstall, PLATFORM_BY_NAME, PLATFORMS

    resolved = _resolve_path(path)

    if platform and platform not in PLATFORM_BY_NAME:
        valid = ", ".join(p.name for p in PLATFORMS)
        err_console.print(f"[red]Error:[/red] Unknown platform '{platform}'. Valid: {valid}")
        raise typer.Exit(1)

    platform_names = [platform] if platform else None
    results = do_uninstall(resolved, platform_names, remove_skeleton=clean)

    if not results:
        console.print("[yellow]No rtt sections found in any config files.[/yellow]")
        return

    for r in results:
        console.print(f"  [green]cleaned[/green]  {r.config_file}")

    if clean:
        console.print(f"  [green]removed[/green]  .rtt/context.txt")

    console.print(f"\n[bold green]Uninstalled from {len(results)} file(s).[/bold green]")


@app.command()
def view(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write markdown to file instead of pager"),
):
    """Render the repo skeleton as human-readable markdown."""
    from rtt.extractor import extract_repo
    from rtt.formatter import format_markdown
    import subprocess

    resolved = _resolve_path(path)

    with console.status("[dim]Building index...[/dim]", spinner="dots"):
        repo = extract_repo(resolved)

    md = format_markdown(repo)

    if output:
        Path(output).write_text(md)
        console.print(f"[green]Markdown written to {output}[/green]")
    else:
        try:
            pager = subprocess.Popen(["glow", "-"], stdin=subprocess.PIPE)
            pager.communicate(input=md.encode())
        except FileNotFoundError:
            print(md)


@app.command()
def vs(
    path: str = typer.Argument(".", help="Path to repo or directory"),
    tool: str = typer.Option("graphify", "--tool", "-t", help="Tool to compare against (currently: graphify)"),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Keep the tool's output directory after comparison"),
):
    """Compare rtt's token footprint against another repo-indexing tool.

    Runs the chosen tool on the same repo, counts tokens in both outputs,
    and prints a side-by-side comparison.

    Currently supported tools: graphify (pip install graphifyy)
    """
    import subprocess
    import shutil
    import tempfile
    from rtt.extractor import extract_repo
    from rtt.tokenizer import count_tokens
    from rtt.formatter import format_text

    resolved = _resolve_path(path)

    if tool.lower() != "graphify":
        err_console.print(f"[red]Error:[/red] Unknown tool '{tool}'. Currently only 'graphify' is supported.")
        raise typer.Exit(1)

    # ── check graphify is available ───────────────────────────────────────────
    # Also check next to the running Python (covers venv installs not on PATH)
    import sys
    graphify_bin = shutil.which("graphify")
    if not graphify_bin:
        candidate = Path(sys.executable).parent / "graphify"
        if candidate.exists():
            graphify_bin = str(candidate)
    if not graphify_bin:
        err_console.print(
            "[red]Error:[/red] 'graphify' not found in PATH.\n"
            "Install it with: [bold]pip install graphifyy[/bold]"
        )
        raise typer.Exit(1)

    # ── rtt token count ───────────────────────────────────────────────────────
    with console.status("[dim]Indexing with rtt...[/dim]", spinner="dots"):
        repo = extract_repo(resolved, use_cache=False)
        rtt_text = format_text(repo)
        rtt_tokens = count_tokens(rtt_text)
        rtt_files = len(repo.files)

    # ── graphify run ──────────────────────────────────────────────────────────
    out_dir = Path(resolved) / "graphify-out"
    pre_existing = out_dir.exists()

    with console.status("[dim]Indexing with graphify (this may take a moment)...[/dim]", spinner="dots"):
        result = subprocess.run(
            [graphify_bin, "update", resolved],
            capture_output=True,
            text=True,
            cwd=resolved,
        )

    if result.returncode != 0:
        err_console.print(f"[red]graphify failed:[/red]\n{result.stderr.strip()}")
        raise typer.Exit(1)

    # ── count graphify output tokens ──────────────────────────────────────────
    graphify_tokens: dict[str, int] = {}

    report_md = out_dir / "GRAPH_REPORT.md"
    graph_json = out_dir / "graph.json"

    if report_md.exists():
        graphify_tokens["GRAPH_REPORT.md"] = count_tokens(report_md.read_text(errors="replace"))
    if graph_json.exists():
        graphify_tokens["graph.json"] = count_tokens(graph_json.read_text(errors="replace"))

    if not graphify_tokens:
        err_console.print("[yellow]Warning:[/yellow] graphify ran but produced no output files in graphify-out/.")
        raise typer.Exit(1)

    # The canonical "LLM context" file from graphify is GRAPH_REPORT.md;
    # fall back to graph.json if only that exists.
    graphify_primary = graphify_tokens.get("GRAPH_REPORT.md") or graphify_tokens.get("graph.json", 0)
    graphify_primary_name = "GRAPH_REPORT.md" if "GRAPH_REPORT.md" in graphify_tokens else "graph.json"

    # ── cleanup ───────────────────────────────────────────────────────────────
    if not no_cleanup and not pre_existing and out_dir.exists():
        shutil.rmtree(out_dir)

    # ── display results ───────────────────────────────────────────────────────
    report_tokens = graphify_tokens.get("GRAPH_REPORT.md", 0)
    json_tokens   = graphify_tokens.get("graph.json", 0)

    console.print()
    console.print("[bold]rtt vs graphify - Token Comparison[/bold]")
    console.print("─" * 64)
    console.print(f"  Repo:     [dim]{resolved}[/dim]")
    console.print(f"  Files:    {rtt_files}")
    console.print()

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Output", style="bold")
    table.add_column("Tokens", justify="right")
    table.add_column("Notes", style="dim")

    table.add_row("rtt skeleton index", f"[green]{rtt_tokens:,}[/green]",
                  "complete API surface - every signature & import")

    if report_tokens:
        diff_report = (1 - rtt_tokens / report_tokens) * 100
        dir_str = f"rtt is {abs(diff_report):.0f}% {'smaller' if diff_report > 0 else 'larger'}"
        table.add_row("graphify GRAPH_REPORT.md", f"{report_tokens:,}",
                      f"high-level summary only - {dir_str}")

    if json_tokens:
        diff_json = (1 - rtt_tokens / json_tokens) * 100
        dir_str2 = f"rtt is {abs(diff_json):.0f}% {'smaller' if diff_json > 0 else 'larger'}"
        table.add_row("graphify graph.json", f"{json_tokens:,}",
                      f"full graph (impractical for LLMs) - {dir_str2}")

    console.print(table)
    console.print()

    if report_tokens:
        if rtt_tokens < report_tokens:
            console.print(
                f"  vs GRAPH_REPORT.md: [bold green]rtt is {(1 - rtt_tokens/report_tokens)*100:.0f}% smaller[/bold green] "
                f"and retains full structural detail"
            )
        else:
            console.print(
                f"  vs GRAPH_REPORT.md: graphify's report is {(1 - report_tokens/rtt_tokens)*100:.0f}% smaller "
                f"[dim](high-level summary - rtt preserves complete API surface)[/dim]"
            )
    if json_tokens and rtt_tokens < json_tokens:
        ratio = json_tokens / rtt_tokens
        console.print(
            f"  vs graph.json:      [bold green]rtt is {ratio:.0f}x smaller[/bold green] "
            f"than the full graphify graph"
        )

    if not no_cleanup and not pre_existing:
        console.print(f"\n  [dim]graphify-out/ removed (use --no-cleanup to keep)[/dim]")
    console.print()


if __name__ == "__main__":
    app()
