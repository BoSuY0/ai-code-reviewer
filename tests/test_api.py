from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.models.schemas import ReviewResult, StaticReport
from src.services.ai_agent import ReviewError
from src.services.github import GitHubError

client = TestClient(app)
FAKE = ReviewResult(summary="ok", issues=[], score=9)


@pytest.fixture
def llm_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(files: dict[str, str], static: StaticReport) -> ReviewResult:
        calls.append({"files": files, "static": static})
        return FAKE

    monkeypatch.setattr("src.main.generate_review", fake)
    return calls


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": app.version}


def test_review_code_combines_static_analysis_and_llm(
    llm_calls: list[dict[str, Any]],
) -> None:
    response = client.post("/review", json={"code": "import os\n"})
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 9
    assert body["files"] == ["submitted.py"]
    codes = [f["code"] for f in body["static_analysis"]["findings"]]
    assert codes == ["F401"]
    assert llm_calls[0]["static"].findings[0].code == "F401"


@pytest.mark.parametrize(
    "payload",
    [
        {"code": ""},
        {},
        {"code": "x = 1", "pr_url": "https://github.com/a/b/pull/1"},
        {"pr_url": "https://evil.com/a/b/pull/1"},
        {"code": "x" * 20_001},
    ],
)
def test_invalid_input_is_422(
    payload: dict[str, Any], llm_calls: list[dict[str, Any]]
) -> None:
    assert client.post("/review", json=payload).status_code == 422
    assert llm_calls == []


def test_llm_failure_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(files: dict[str, str], static: StaticReport) -> ReviewResult:
        raise ReviewError("LLM API unavailable after 4 attempts")

    monkeypatch.setattr("src.main.generate_review", broken)
    response = client.post("/review", json={"code": "x = 1\n"})
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]


def test_pull_request_review(
    monkeypatch: pytest.MonkeyPatch, llm_calls: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(
        "src.main.fetch_pr_files", lambda url: {"pkg/app.py": "X: int = 1\n"}
    )
    url = "https://github.com/octo/demo/pull/7"
    response = client.post("/review", json={"pr_url": url})
    assert response.status_code == 200
    assert response.json()["files"] == ["pkg/app.py"]
    assert llm_calls[0]["files"] == {"pkg/app.py": "X: int = 1\n"}


@pytest.mark.parametrize("status", [404, 413, 422, 502])
def test_github_errors_keep_their_status(
    monkeypatch: pytest.MonkeyPatch, llm_calls: list[dict[str, Any]], status: int
) -> None:
    def broken(url: str) -> dict[str, str]:
        raise GitHubError("nope", status)

    monkeypatch.setattr("src.main.fetch_pr_files", broken)
    response = client.post(
        "/review", json={"pr_url": "https://github.com/octo/demo/pull/7"}
    )
    assert response.status_code == status
    assert llm_calls == []
