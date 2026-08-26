#!/usr/bin/env python3
"""PPT Master - Repository Updater

Pull the latest Git checkout and sync Python dependencies when the effective
requirements include tree changes.

Usage:
    skills/ppt-master/.venv/bin/python3 skills/ppt-master/scripts/update_repo.py
    skills/ppt-master/.venv/bin/python3 skills/ppt-master/scripts/update_repo.py --skip-deps
"""

from __future__ import annotations

import argparse
import hashlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from console_encoding import configure_utf8_stdio

configure_utf8_stdio()


TOOLS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TOOLS_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent
REQUIREMENTS_FILE = SKILL_DIR / "requirements.txt"
VENV_DIR = SKILL_DIR / ".venv"


def non_git_checkout_message() -> str:
    return f"""This copy of PPT Master is not a Git checkout, so it cannot be updated automatically.

Repository path:
  {REPO_ROOT}

If you installed with Download ZIP:
  1. Download the latest ZIP from GitHub or AtomGit.
  2. Unzip it into a new folder.
  3. Copy your old .env and projects/ folder into the new folder.
  4. Run: pip install -r requirements.txt

If you want one-command updates next time, install with Git clone:
  git clone https://github.com/hugohe3/ppt-master.git

If you installed through a skill marketplace, update or reinstall through the
same marketplace / skills tool."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pull the latest repository changes and sync Python dependencies "
            "only when the requirements include tree changes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-deps",
        "--skip-pip",
        action="store_true",
        dest="skip_deps",
        help="Skip Python dependency sync even if the requirements include tree changed.",
    )
    return parser.parse_args(argv)


def print_status(message: str = "") -> None:
    """Print progress/status messages to stderr."""
    print(message, file=sys.stderr)


def run_command(
    args: list[str], *, check: bool = True, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd or REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _requirement_includes(path: Path, content: str) -> list[Path]:
    """Resolve local -r/--requirement includes relative to their owning file."""
    includes: list[Path] = []
    for raw_line in content.splitlines():
        try:
            tokens = shlex.split(raw_line, comments=True, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue

        option = tokens[0]
        include_value: str | None = None
        if option in {"-r", "--requirement"}:
            if len(tokens) >= 2:
                include_value = tokens[1]
        elif option.startswith("--requirement="):
            include_value = option.partition("=")[2]
        elif option.startswith("-r") and len(option) > 2:
            include_value = option[2:].lstrip("=")

        if not include_value:
            continue
        include_path = Path(include_value)
        if not include_path.is_absolute():
            include_path = path.parent / include_path
        includes.append(include_path)
    return includes


def requirements_digest(path: Path) -> str | None:
    """Hash one requirements file and its recursive local include closure."""
    if not path.exists():
        return None

    digest = hashlib.sha256()
    visited: set[Path] = set()

    def visit(current: Path) -> None:
        try:
            resolved = current.resolve()
        except OSError as exc:
            raise RuntimeError(
                f"Unable to resolve requirements file {current}: {exc}"
            ) from exc
        if resolved in visited:
            digest.update(b"repeat\0")
            return
        visited.add(resolved)

        if not resolved.is_file():
            digest.update(b"missing\0")
            return
        try:
            content = resolved.read_bytes()
        except OSError as exc:
            raise RuntimeError(
                f"Unable to read requirements file {resolved}: {exc}"
            ) from exc
        digest.update(b"file\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        for included in _requirement_includes(
            resolved,
            content.decode("utf-8", errors="replace"),
        ):
            visit(included)

    visit(path)
    return digest.hexdigest()


def ensure_git_available() -> None:
    if shutil.which("git") is None:
        raise RuntimeError("Missing executable: git")


def ensure_git_checkout() -> None:
    if not (REPO_ROOT / ".git").exists():
        raise RuntimeError(non_git_checkout_message())


def ensure_clean_tracked_worktree() -> None:
    status = run_command(["git", "status", "--porcelain", "--untracked-files=no"], check=False)
    if status.returncode != 0:
        details = (status.stderr or status.stdout or "").strip()
        raise RuntimeError(details or "Unable to inspect git status.")

    if status.stdout.strip():
        raise RuntimeError(
            "Tracked local changes detected. Please commit or stash them before running the update command."
        )


def get_head_revision() -> str:
    result = run_command(["git", "rev-parse", "HEAD"])
    return result.stdout.strip()


def sync_python_dependencies() -> None:
    if not REQUIREMENTS_FILE.exists():
        print_status("requirements.txt not found; skipping Python dependency sync.")
        return

    if shutil.which("uv") is None:
        raise RuntimeError(
            "uv is not installed. Install it first:\n"
            "  macOS:  brew install uv\n"
            "  Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh"
        )

    if not VENV_DIR.exists():
        print(".venv not found. Creating virtual environment with uv...")
        run_command(["uv", "venv", str(VENV_DIR)], cwd=SKILL_DIR)

    python_bin = str(VENV_DIR / "bin" / "python3")
    print("Requirements include tree changed. Syncing Python dependencies with uv...")
    result = run_command(
        ["uv", "pip", "install", "-r", str(REQUIREMENTS_FILE), "--python", python_bin],
        cwd=SKILL_DIR,
    )
    if result.stdout.strip():
        print_status(result.stdout.strip())
    if result.stderr.strip():
        print_status(result.stderr.strip())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        ensure_git_checkout()
        ensure_git_available()
        ensure_clean_tracked_worktree()

        before_head = get_head_revision()
        before_requirements = requirements_digest(REQUIREMENTS_FILE)

        print_status(f"Repository: {REPO_ROOT}")
        pull_result = run_command(["git", "pull", "--ff-only"])
        if pull_result.stdout.strip():
            print_status(pull_result.stdout.strip())
        if pull_result.stderr.strip():
            print_status(pull_result.stderr.strip())

        after_head = get_head_revision()
        after_requirements = requirements_digest(REQUIREMENTS_FILE)

        if before_head == after_head:
            print_status("Repository is already up to date.")
        else:
            print_status(f"Updated from {before_head[:7]} to {after_head[:7]}.")

        if args.skip_deps:
            print("Skipped Python dependency sync (--skip-deps).")
        elif before_requirements != after_requirements:
            sync_python_dependencies()
        else:
            print_status(
                "Requirements include tree unchanged. Skipping Python dependency sync."
            )

        print_status(
            "Note: system dependencies such as Node.js and Pandoc still need "
            "to be installed manually."
        )
        return 0
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        print(details or "Command failed.", file=sys.stderr)
        return exc.returncode or 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
