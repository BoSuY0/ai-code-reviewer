import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.services import analyzer
from src.services.analyzer import UnsafePathError, run_static_analysis

GOOD = """def add(a: int, b: int) -> int:
    return a + b
"""
BAD = """import os
def add(a, b):
    total=a + b
    return total
"""


def test_good_code_has_no_findings() -> None:
    report = run_static_analysis({"submitted.py": GOOD})
    assert report.findings == []
    assert report.errors == []


def test_bad_code_reports_both_tools() -> None:
    report = run_static_analysis({"submitted.py": BAD})
    codes = {(f.tool, f.code, f.line) for f in report.findings}
    assert ("flake8", "F401", 1) in codes
    assert ("flake8", "E302", 2) in codes
    assert ("flake8", "E225", 3) in codes
    assert ("mypy", "no-untyped-def", 2) in codes
    assert all(f.file == "submitted.py" for f in report.findings)


def test_mypy_sees_packages_and_reports_paths() -> None:
    files = {
        "pkg/a/utils.py": "X: int = 1\n",
        "pkg/b/utils.py": 'Y: int = "s"\n',
    }
    report = run_static_analysis(files)
    assert [(f.file, f.code) for f in report.findings] == [
        ("pkg/b/utils.py", "assignment")
    ]
    assert report.errors == []


def test_syntax_error_is_a_finding_not_a_crash() -> None:
    report = run_static_analysis({"bad.py": "def (:\n"})
    assert {f.code for f in report.findings} >= {"E999", "syntax"}
    assert report.errors == []


def test_submitted_code_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    code = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(3)\n"
    run_static_analysis({"submitted.py": code})
    assert not marker.exists()


@pytest.mark.parametrize("name", ["../x.py", "/etc/x.py", "a/./b.py", "x.txt"])
def test_unsafe_file_names_are_refused(name: str) -> None:
    with pytest.raises(UnsafePathError):
        run_static_analysis({name: GOOD})


def test_tools_run_without_shell_or_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    seen: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("src.services.analyzer.subprocess.run", fake_run)
    run_static_analysis({"submitted.py": GOOD})
    assert [call["cmd"][2] for call in seen] == ["flake8", "mypy"]
    for call in seen:
        assert call["shell"] is False
        assert call["timeout"] == analyzer.TIMEOUT_SECONDS
        assert "OPENROUTER_API_KEY" not in call["env"]
        assert Path(call["cwd"]).name.startswith("review-")


def test_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(cmd: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr("src.services.analyzer.subprocess.run", slow)
    report = run_static_analysis({"submitted.py": GOOD}, timeout=0.5)
    assert report.errors == [
        "flake8 timed out after 0.5 s",
        "mypy timed out after 0.5 s",
    ]


def test_tool_crash_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = iter([(2, "", "flake8: boom\nmore"), (2, "", "")])

    def broken(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        code, out, err = next(outputs)
        return subprocess.CompletedProcess(cmd, code, out, err)

    monkeypatch.setattr("src.services.analyzer.subprocess.run", broken)
    report = run_static_analysis({"submitted.py": GOOD})
    assert report.errors == ["flake8 failed: flake8: boom", "mypy failed: 2"]
