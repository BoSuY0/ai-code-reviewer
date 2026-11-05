"""Pydantic models for the API request, the LLM review and the final report."""

import re
from enum import Enum


from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from typing_extensions import Self

MAX_CODE_CHARS = 20_000
# GitHub owners are letters, digits and "-"; repo names may not be "." or "..".
PR_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9-]+)/"
    r"(?P<repo>(?!\.\.?/)[\w.-]+)/pull/(?P<number>\d+)$",
    re.ASCII,
)
# Relative POSIX path of a .py file: no leading "/", no "." or ".." segments.
# Relative path of a .py file: no "." or ".." segments and no segment that
# starts with "-", so a name can never be read as a command-line option.
SAFE_FILE_PATH = re.compile(r"^(?:(?!\.\.?/|-)[\w.-]+/)*(?!-)[\w.-]+\.py$", re.ASCII)


class ReviewRequest(BaseModel):
    """Exactly one of ``code`` or ``pr_url`` must be given."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=MAX_CODE_CHARS)
    pr_url: str | None = Field(default=None, max_length=200)

    @field_validator("code")
    @classmethod
    def code_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("code must not be blank")
        return value

    @field_validator("pr_url")
    @classmethod
    def github_pr_only(cls, value: str | None) -> str | None:
        if value is not None and not PR_URL_PATTERN.fullmatch(value):
            raise ValueError(
                "pr_url must look like https://github.com/<owner>/<repo>/pull/<n>"
            )
        return value

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if (self.code is None) == (self.pr_url is None):
            raise ValueError("send exactly one of 'code' or 'pr_url'")
        return self


class StaticFinding(BaseModel):
    tool: str
    file: str
    line: int
    column: int | None = None
    code: str
    message: str


class StaticReport(BaseModel):
    findings: list[StaticFinding] = []
    errors: list[str] = Field(
        default=[], description="Analyzer failures such as timeouts"
    )


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    line: int = Field(ge=1, description="1-based line number in that file")
    severity: Severity
    message: str = Field(min_length=1)
    suggestion: str

    @model_validator(mode="after")
    def points_into_reviewed_code(self, info: ValidationInfo) -> Self:
        files: dict[str, int] | None = (info.context or {}).get("files")
        if files is None:
            return self
        if self.file not in files:
            raise ValueError(f"unknown file {self.file!r}")
        if self.line > files[self.file]:
            raise ValueError(
                f"line {self.line} is outside {self.file} (1..{files[self.file]})"
            )
        return self


class ReviewResult(BaseModel):
    """What the LLM must return."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    issues: list[Issue]
    score: int = Field(ge=0, le=10, description="Overall quality, 10 is best")


class ReviewResponse(ReviewResult):
    """What the API returns: the LLM review plus the raw static findings."""

    files: list[str]
    static_analysis: StaticReport
