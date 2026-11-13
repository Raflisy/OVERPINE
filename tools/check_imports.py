"""Check project Python imports and report frontend external URLs."""
from __future__ import annotations

import ast
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

_EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "overpine",
    "venv",
}
_EXTERNAL_URL = re.compile(
    r"https?://[^\s\"'<>`]+|(?<!:)//[A-Za-z0-9][^\s\"'<>`]*"
)


def project_modules(root: Path) -> set[str]:
    """Find importable top-level modules and packages in the source tree."""
    modules: set[str] = set()
    for path in root.glob("*.py"):
        modules.add(path.stem)
    for path in root.iterdir():
        if path.is_dir() and path.name not in _EXCLUDED_DIRECTORIES:
            if (path / "__init__.py").is_file() or path.name.isidentifier():
                modules.add(path.name)
    return modules


def _python_source_files(root: Path) -> Iterator[Path]:
    """Yield source files without descending into excluded build or VCS trees."""
    for current, directories, filenames in os.walk(root):
        current_path = Path(current)
        if current_path != root and (current_path / ".git").exists():
            directories.clear()
            continue
        directories[:] = sorted(
            name for name in directories if name not in _EXCLUDED_DIRECTORIES
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield current_path / filename


def check_python_imports(root: Path) -> list[str]:
    """Return all imports that are neither standard-library nor project modules."""
    allowed = set(sys.stdlib_module_names) | project_modules(root)
    errors: list[str] = []
    for path in _python_source_files(root):
        relative = path.relative_to(root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{relative}: could not inspect Python source: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                imported = [node.module] if node.module else []
            else:
                continue
            for name in imported:
                top_level = name.split(".", 1)[0]
                if top_level not in allowed:
                    errors.append(f"{relative}:{node.lineno}: third-party import {name}")
    return errors


def frontend_external_urls(root: Path) -> list[str]:
    """Return URL-like references in frontend files for a manual audit."""
    frontend = root / "frontend"
    findings: list[str] = []
    if not frontend.is_dir():
        return findings
    for path in sorted(frontend.rglob("*")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(root)
        for line_number, line in enumerate(lines, start=1):
            for match in _EXTERNAL_URL.finditer(line):
                url = match.group(0).rstrip("),;.")
                findings.append(f"{relative}:{line_number}: {url}")
    return findings


def main() -> int:
    """Print import failures and frontend URL findings."""
    root = Path(__file__).resolve().parents[1]
    errors = check_python_imports(root)
    urls = frontend_external_urls(root)

    print("Third-party Python imports:")
    if errors:
        for error in errors:
            print(f"  {error}")
    else:
        print("  None found.")

    print("External URL references in frontend:")
    if urls:
        for finding in urls:
            print(f"  {finding}")
    else:
        print("  None found.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
