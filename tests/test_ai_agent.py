from collections.abc import Callable
from typing import Any

import pytest

from src.models.schemas import Severity, StaticFinding, StaticReport
from src.services import ai_agent
from src.services.ai_agent import ReviewError, generate_review
from tests.conftest import (
    VALID_JSON,
    FakeClient,
    auth_error,
    connection_error,
    rate_limit,
)

FILES = {"submitted.py": "import sqlite3\ncur.execute('SELECT ' + name)\n"}
STATIC = StaticReport(
    findings=[
        StaticFinding(
            tool="flake8",
            file="submitted.py",
            line=2,
            column=1,
            code="F821",
            message="undefined name 'cur'",
        )
    ]
)
Factory = Callable[[list[Any]], FakeClient]


def review(client: FakeClient, **kwargs: Any) -> Any:
    fake: Any = client  # duck-typed stand-in for openai.OpenAI
    return generate_review(FILES, STATIC, client=fake, **kwargs)


def test_valid_review_is_parsed(fake_client: Factory) -> None:
    client = fake_client([VALID_JSON])
    result = review(client, model="m")
    assert result.score == 4
    assert result.issues[0].severity is Severity.HIGH
    call = client.calls[0]
    assert call["model"] == "m"
    assert call["temperature"] == ai_agent.TEMPERATURE
    assert call["response_format"]["json_schema"]["strict"] is True


def test_prompt_has_static_findings_and_untrusted_code(fake_client: Factory) -> None:
    client = fake_client([VALID_JSON])
    review(client)
    system, user = (m["content"] for m in client.calls[0]["messages"])
    assert "untrusted" in system
    assert '"code": "F821"' in user
    assert '<file path="submitted.py">\n1| import sqlite3' in user
    assert user.endswith("</file>")


def test_model_comes_from_environment(
    fake_client: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL", "env/model")
    client = fake_client([VALID_JSON])
    review(client)
    assert client.calls[0]["model"] == "env/model"


def test_fenced_json_is_accepted(fake_client: Factory) -> None:
    result = review(fake_client([f"```json\n{VALID_JSON}\n```"]))
    assert result.summary == "Unsafe SQL."


def test_rate_limit_is_retried_with_backoff(fake_client: Factory) -> None:
    delays: list[float] = []
    client = fake_client([rate_limit(), connection_error(), VALID_JSON])
    result = review(client, base_delay=1.0, sleep=delays.append)
    assert result.score == 4
    assert len(client.calls) == 3
    assert 1.0 <= delays[0] < 2.0 and 2.0 <= delays[1] < 3.0


def test_retries_are_bounded(fake_client: Factory) -> None:
    client = fake_client([rate_limit()] * 3)
    with pytest.raises(ReviewError, match="after 3 attempts"):
        review(client, retries=3, sleep=lambda _: None)
    assert len(client.calls) == 3


def test_auth_error_is_not_retried(fake_client: Factory) -> None:
    client = fake_client([auth_error()])
    with pytest.raises(ReviewError, match="LLM API error 401"):
        review(client, sleep=lambda _: None)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "",
        VALID_JSON.replace('"line": 2', '"line": 99'),
        VALID_JSON.replace("submitted.py", "other.py"),
        VALID_JSON.replace('"score": 4', '"score": 11'),
    ],
)
def test_invalid_review_raises(fake_client: Factory, content: str) -> None:
    with pytest.raises(ReviewError, match="invalid review"):
        review(fake_client([content]))


def test_retries_must_be_positive(fake_client: Factory) -> None:
    with pytest.raises(ValueError):
        review(fake_client([]), retries=0)


def test_missing_key_is_a_review_error() -> None:
    with pytest.raises(ReviewError, match="OPENROUTER_API_KEY"):
        ai_agent.make_client()


def test_client_uses_openrouter_without_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = ai_agent.make_client()
    assert str(client.base_url).startswith(ai_agent.BASE_URL)
    assert client.max_retries == 0
