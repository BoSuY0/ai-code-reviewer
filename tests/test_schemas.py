import pytest
from pydantic import ValidationError

from src.models.schemas import MAX_CODE_CHARS, ReviewRequest, ReviewResult


def test_code_request_is_valid() -> None:
    assert ReviewRequest(code="x = 1\n").code == "x = 1\n"


def test_pr_request_is_valid() -> None:
    url = "https://github.com/BoSuY0/ai-code-reviewer/pull/1"
    assert ReviewRequest(pr_url=url).pr_url == url


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"code": "x = 1", "pr_url": "https://github.com/a/b/pull/1"},
        {"code": ""},
        {"code": "   \n\t"},
        {"code": "x" * (MAX_CODE_CHARS + 1)},
        {"code": "x = 1", "extra": True},
    ],
)
def test_invalid_code_requests(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(payload)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b/pull/1",
        "https://github.com.evil.com/a/b/pull/1",
        "https://evil.com/github.com/a/b/pull/1",
        "https://github.com@evil.com/a/b/pull/1",
        "https://github.com/a/b/pull/1?x=https://evil.com",
        "https://github.com/a/b/pull/1#frag",
        "https://github.com/a/b/pull/1/files",
        "https://github.com/a/b/pull/1\n",
        "https://github.com/a/b/pull/１",
        "https://github.com/a/b/issues/1",
        "https://localhost/a/b/pull/1",
        "https://github.com/../b/pull/1",
        "https://github.com/a/../pull/1",
        "https://github.com/a/./pull/1",
    ],
)
def test_pr_url_rejects_other_hosts_and_shapes(url: str) -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(pr_url=url)


def test_issue_must_point_into_reviewed_file() -> None:
    issue = '{"file": "%s", "line": %d, "severity": "low", "message": "m", '
    issue += '"suggestion": "s"}'
    template = '{"summary": "s", "score": 5, "issues": [%s]}'
    context = {"files": {"a.py": 3}}

    ok = template % (issue % ("a.py", 3))
    assert ReviewResult.model_validate_json(ok, context=context).issues[0].line == 3
    for bad in (issue % ("a.py", 4), issue % ("b.py", 1)):
        with pytest.raises(ValidationError):
            ReviewResult.model_validate_json(template % bad, context=context)
