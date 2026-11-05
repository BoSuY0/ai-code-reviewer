"""Run flake8 and mypy over submitted files without executing them.

Safety rules: the code is only written to a fresh temporary directory, the
tools run as ``python -m <tool>`` with ``shell=False``, a timeout, no config
files and an environment that carries no secrets.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from src.models.schemas import SAFE_FILE_PATH, StaticFinding, StaticReport

TIMEOUT_SECONDS = 30
FLAKE8_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): (?P<code>\w+) (?P<msg>.*)$"
)
MYPY_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): error: (?P<msg>.*?)"
    r"(?:  \[(?P<code>[\w-]+)\])?$"
)


class UnsafePathError(ValueError):
    """A file name could escape the temporary directory."""


def _check_path(name: str) -> None:
    if not SAFE_FILE_PATH.fullmatch(name):
        raise UnsafePathError(f"refusing to analyze file name {name!r}")


def _command(tool: str, tmp: Path, files: list[str]) -> list[str]:
    if tool == "flake8":
        return [
            sys.executable,
            "-I",
            "-m",
            "flake8",
            "--isolated",
            "--max-line-length=88",
            "--",
            *files,
        ]
    return [
        sys.executable,
        "-I",
        "-m",
        "mypy",
        "--strict",
        "--config-file=",
        f"--cache-dir={tmp / '.mypy_cache'}",
        "--explicit-package-bases",
        "--show-column-numbers",
        "--no-error-summary",
        "--no-color-output",
        "--",
        *files,
    ]


def _parse(tool: str, output: str) -> list[StaticFinding]:
    pattern = FLAKE8_LINE if tool == "flake8" else MYPY_LINE
    findings = []
    for raw in output.splitlines():
        match = pattern.match(raw)
        if match is None:
            continue
        findings.append(
            StaticFinding(
                tool=tool,
                file=PurePosixPath(match["file"]).as_posix(),
                line=int(match["line"]),
                column=int(match["col"]),
                code=match["code"] or "mypy",
                message=match["msg"],
            )
        )
    return findings


def run_static_analysis(
    files: dict[str, str], timeout: float = TIMEOUT_SECONDS
) -> StaticReport:
    """Return flake8 and mypy findings for ``{relative path: source}``."""
    for name in files:
        _check_path(name)
    report = StaticReport()
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="review-") as tmp_name:
        tmp = Path(tmp_name)
        for name, source in files.items():
            target = tmp / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        names = sorted(files)
        for tool in ("flake8", "mypy"):
            try:
                result = subprocess.run(
                    _command(tool, tmp, names),
                    cwd=tmp,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    shell=False,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                report.errors.append(f"{tool} timed out after {timeout:g} s")
                continue
            findings = _parse(tool, result.stdout)
            # Both tools exit with 1 on findings (mypy uses 2 for syntax
            # errors); an unparsed non-zero exit means the tool itself failed.
            if result.returncode != 0 and not findings:
                detail = (result.stderr or result.stdout).strip().splitlines()
                report.errors.append(
                    f"{tool} failed: {detail[0] if detail else result.returncode}"
                )
            report.findings.extend(findings)
    return report
