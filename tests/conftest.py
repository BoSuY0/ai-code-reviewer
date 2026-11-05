from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import APIConnectionError, AuthenticationError, RateLimitError

# openai>=3 is built on httpx2, so its errors need httpx2 objects.
REQUEST = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")

VALID_JSON = (
    '{"summary": "Unsafe SQL.", "score": 4, "issues": [{"file": "submitted.py", '
    '"line": 2, "severity": "high", "message": "SQL injection", '
    '"suggestion": "Use parameters"}]}'
)


def rate_limit() -> RateLimitError:
    return RateLimitError(
        "rate limited", response=httpx2.Response(429, request=REQUEST), body=None
    )


def auth_error() -> AuthenticationError:
    return AuthenticationError(
        "invalid key", response=httpx2.Response(401, request=REQUEST), body=None
    )


def connection_error() -> APIConnectionError:
    return APIConnectionError(request=REQUEST)


class FakeClient:
    """Mimics client.chat.completions.create with scripted outcomes."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = SimpleNamespace(content=outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_client() -> Callable[[list[Any]], FakeClient]:
    return FakeClient


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a real API, so hide keys from .env and the shell."""
    monkeypatch.setattr("src.services.ai_agent.load_dotenv", lambda: False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
