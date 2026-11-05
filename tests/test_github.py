import base64
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from src.services.github import MAX_FILES, GitHubError, fetch_pr_files

URL = "https://github.com/octo/demo/pull/7"
Handler = Callable[[httpx.Request], httpx.Response]


def content(text: str) -> dict[str, object]:
    encoded = base64.b64encode(text.encode()).decode()
    return {"encoding": "base64", "content": encoded, "size": len(text)}


def github(files: list[dict[str, str]], sources: dict[str, str]) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        path = request.url.path
        if path == "/repos/octo/demo/pulls/7":
            return httpx.Response(200, json={"head": {"sha": "abc123"}})
        if path == "/repos/octo/demo/pulls/7/files":
            return httpx.Response(200, json=files)
        name = path.removeprefix("/repos/octo/demo/contents/")
        assert request.url.params["ref"] == "abc123"
        return httpx.Response(200, json=content(sources[name]))

    return handle


def fetch(handler: Handler) -> dict[str, str]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return fetch_pr_files(URL, client=client)


def test_fetches_changed_python_files_only() -> None:
    files = [
        {"filename": "src/app.py", "status": "modified"},
        {"filename": "README.md", "status": "modified"},
        {"filename": "old.py", "status": "removed"},
    ]
    assert fetch(github(files, {"src/app.py": "x = 1\n"})) == {"src/app.py": "x = 1\n"}


@pytest.mark.parametrize(
    ("files", "status", "message"),
    [
        ([{"filename": "README.md", "status": "added"}], 422, "no Python files"),
        ([{"filename": "../x.py", "status": "added"}], 422, "unsupported file"),
        (
            [{"filename": f"f{i}.py", "status": "added"} for i in range(MAX_FILES + 1)],
            413,
            "more than",
        ),
    ],
)
def test_rejected_pull_requests(
    files: list[dict[str, str]], status: int, message: str
) -> None:
    with pytest.raises(GitHubError, match=message) as info:
        fetch(github(files, {}))
    assert info.value.status == status


def test_total_size_is_limited() -> None:
    files = [{"filename": "big.py", "status": "added"}]
    with pytest.raises(GitHubError, match="exceed") as info:
        fetch(github(files, {"big.py": "x" * 20_001}))
    assert info.value.status == 413


def test_non_base64_content_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[{"filename": "a.py", "status": "added"}])
        if "/contents/" in request.url.path:
            return httpx.Response(200, json={"encoding": "none", "size": 1})
        return httpx.Response(200, json={"head": {"sha": "abc123"}})

    with pytest.raises(GitHubError, match="cannot read a.py") as info:
        fetch(handler)
    assert info.value.status == 502


@pytest.mark.parametrize(("code", "status"), [(404, 404), (403, 502), (500, 502)])
def test_github_errors_are_mapped(code: int, status: int) -> None:
    with pytest.raises(GitHubError) as info:
        fetch(lambda request: httpx.Response(code))
    assert info.value.status == status


def test_network_error_is_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(GitHubError, match="unreachable") as info:
        fetch(handler)
    assert info.value.status == 502


def test_invalid_url_never_reaches_the_network() -> None:
    with pytest.raises(GitHubError) as info:
        fetch_pr_files("https://evil.com/octo/demo/pull/7")
    assert info.value.status == 422


def test_default_client_sends_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    def client_with_mock(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(httpx, "Client", client_with_mock)
    with pytest.raises(GitHubError):
        fetch_pr_files(URL)
    assert seen[0].headers["Authorization"] == "Bearer test-token"
    assert str(seen[0].url) == "https://api.github.com/repos/octo/demo/pulls/7"
