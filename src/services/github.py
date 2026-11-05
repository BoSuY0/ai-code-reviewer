"""Fetch the Python files of a GitHub pull request.

Only api.github.com is contacted. The URL is rebuilt from the owner, repo and
number that the request regex captured, so user input never chooses the host.
"""

import base64
import os
from typing import Any

import httpx

from src.models.schemas import MAX_CODE_CHARS, PR_URL_PATTERN, SAFE_FILE_PATH

API = "https://api.github.com"
MAX_FILES = 10
TIMEOUT_SECONDS = 15


class GitHubError(RuntimeError):
    """GitHub could not provide the pull request (status is for the client)."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai-reviewer"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(client: httpx.Client, path: str, **params: Any) -> httpx.Response:
    try:
        response = client.get(f"{API}{path}", params=params or None)
    except httpx.HTTPError as exc:
        raise GitHubError(f"GitHub is unreachable: {exc}") from exc
    if response.status_code == 404:
        raise GitHubError("pull request or file not found", status=404)
    if response.status_code != 200:
        raise GitHubError(f"GitHub API error {response.status_code}")
    return response


def fetch_pr_files(
    pr_url: str, *, client: httpx.Client | None = None
) -> dict[str, str]:
    """Return ``{path: source}`` for the added or changed .py files of a PR."""
    match = PR_URL_PATTERN.fullmatch(pr_url)
    if match is None:
        raise GitHubError("not a GitHub pull request URL", status=422)
    repo = f"/repos/{match['owner']}/{match['repo']}"
    own_client = client is None
    client = client or httpx.Client(
        headers=_headers(), timeout=TIMEOUT_SECONDS, follow_redirects=False
    )
    try:
        pr = _get(client, f"{repo}/pulls/{match['number']}").json()
        listing = _get(
            client, f"{repo}/pulls/{match['number']}/files", per_page=100
        ).json()
        paths = [
            item["filename"]
            for item in listing
            if item["filename"].endswith(".py") and item["status"] != "removed"
        ]
        unsafe = [path for path in paths if not SAFE_FILE_PATH.fullmatch(path)]
        if unsafe:
            raise GitHubError(f"unsupported file name {unsafe[0]!r}", 422)
        if not paths:
            raise GitHubError("the pull request changes no Python files", 422)
        if len(paths) > MAX_FILES:
            raise GitHubError(f"more than {MAX_FILES} Python files changed", 413)
        files: dict[str, str] = {}
        total = 0
        for path in paths:
            meta = _get(client, f"{repo}/contents/{path}", ref=pr["head"]["sha"]).json()
            total += int(meta["size"])
            if total > MAX_CODE_CHARS:
                raise GitHubError(
                    f"changed Python files exceed {MAX_CODE_CHARS} characters", 413
                )
            if meta.get("encoding") != "base64":
                raise GitHubError(f"cannot read {path}")
            files[path] = base64.b64decode(meta["content"]).decode(
                "utf-8", errors="replace"
            )
        return files
    finally:
        if own_client:
            client.close()
