#!/usr/bin/env python3
"""Fail when likely live credentials or private keys are about to be committed."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
TOKEN_PATTERNS = [
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-(?!your[-_]|example[-_]|test[-_])[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
]
ASSIGNMENT = re.compile(
    r"(?:PG_PASSWORD|ROOT_PASSWORD|PUBLIC_MAP_API_KEY|MAP_SECRET_KEY|OPENAI_API_KEY|WHISPER_API_KEY|SECRET_KEY)\s*[:=]\s*['\"]([^'\"]+)",
    re.IGNORECASE,
)
ENV_ASSIGNMENT = re.compile(
    r"(?:PG_PASSWORD|ROOT_PASSWORD|PUBLIC_MAP_API_KEY|MAP_SECRET_KEY|OPENAI_API_KEY|WHISPER_API_KEY|SECRET_KEY)\s*=\s*([^\s#]+)",
    re.IGNORECASE,
)
SAFE_VALUES = ("${", "$", "<", "change-me", "your-", "sk-your", "example", "replace-", "dummy", "test")


def tracked_files(staged: bool) -> list[Path]:
    command = ["git", "diff", "--cached", "--name-only", "-z"] if staged else ["git", "ls-files", "-z"]
    output = subprocess.run(command, cwd=ROOT, check=True, capture_output=True).stdout
    return [ROOT / name for name in output.decode().split("\0") if name]


def has_live_assignment(path: Path, text: str) -> bool:
    pattern = ENV_ASSIGNMENT if path.name.startswith(".env") else ASSIGNMENT
    for value in pattern.findall(text):
        normalized = value.lower()
        if not any(normalized.startswith(prefix) for prefix in SAFE_VALUES):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true", help="scan staged files only")
    args = parser.parse_args()
    findings: list[str] = []

    for path in tracked_files(args.staged):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PRIVATE_KEY.search(text) or any(pattern.search(text) for pattern in TOKEN_PATTERNS) or has_live_assignment(path, text):
            findings.append(str(path.relative_to(ROOT)))

    if findings:
        print("Potential secret material found in:", ", ".join(findings), file=sys.stderr)
        print("Remove it, rotate any exposed credential, and use environment variables instead.", file=sys.stderr)
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
