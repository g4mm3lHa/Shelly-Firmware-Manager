#!/usr/bin/env python3
"""List the symbols of the backend modules: always current, read from the source.

Usage (repository root):
    python3 tools/dev/symbols.py                    # every module, one line per symbol
    python3 tools/dev/symbols.py manager_checks     # one module (name or path; several allowed)
    python3 tools/dev/symbols.py -g offer           # only symbols whose name contains "offer"
    python3 tools/dev/symbols.py -c manager_core    # classes and module functions only (short)

Each line: ``<line> <kind> <qualified name>(<parameters>) - <first docstring line>``. Read a symbol with
``sed -n '<line>,+40p' <file>``. Kinds: ``class``, ``def`` (function or method), ``async`` (coroutine),
``const`` (module-level UPPER_CASE name). Private names (leading underscore) are listed too.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "shelly_firmware_manager" / "app"
DOC_WIDTH = 100


def first_doc_line(node: ast.AST) -> str:
    """First line of the docstring of a class, function or module ('' if there is none)."""
    doc = ast.get_docstring(node) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None
    line = doc.strip().splitlines()[0] if doc else ""
    return line if len(line) <= DOC_WIDTH else line[: DOC_WIDTH - 1] + "…"


def parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Parameter names of a function without ``self``/``cls`` (names only, no annotations)."""
    args = node.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return ", ".join(n for n in names if n not in ("self", "cls"))


def symbols(path: Path, classes_only: bool) -> list[str]:
    """Output lines for one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []

    def walk(body: list[ast.stmt], prefix: str, top: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                out.append(f"{node.lineno} class {prefix}{node.name} - {first_doc_line(node)}".rstrip(" -"))
                walk(node.body, f"{prefix}{node.name}.", False)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if classes_only and not top:
                    continue
                kind = "async" if isinstance(node, ast.AsyncFunctionDef) else "def"
                head = f"{node.lineno} {kind} {prefix}{node.name}({parameters(node)})"
                doc = first_doc_line(node)
                out.append(f"{head} - {doc}" if doc else head)
            elif top and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isupper() and not classes_only:
                        out.append(f"{node.lineno} const {target.id}")

    walk(tree.body, "", True)
    return out


def resolve(names: list[str]) -> list[Path]:
    """Module files for the given names or paths (all modules when none is given)."""
    if not names:
        return sorted(APP_DIR.glob("*.py"))
    found: list[Path] = []
    for name in names:
        candidate = Path(name)
        if not candidate.is_file():
            candidate = APP_DIR / (name if name.endswith(".py") else name + ".py")
        if not candidate.is_file():
            sys.exit(f"symbols.py: no module {name!r} in {APP_DIR}")
        found.append(candidate)
    return found


def main() -> None:
    """Print the symbol list."""
    parser = argparse.ArgumentParser(description="List classes, functions and constants of backend modules.")
    parser.add_argument("modules", nargs="*", help="module names or paths (default: all)")
    parser.add_argument("-g", "--grep", help="only symbols whose name contains this text (case-insensitive)")
    parser.add_argument("-c", "--short", action="store_true", help="classes and module-level functions only")
    options = parser.parse_args()
    needle = options.grep.lower() if options.grep else None
    for path in resolve(options.modules):
        lines = symbols(path, options.short)
        if needle:
            lines = [ln for ln in lines if needle in ln.split(" - ")[0].lower()]
        if lines:
            print(f"# {path.name}")
            print("\n".join(lines))


if __name__ == "__main__":
    main()
