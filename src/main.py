"""FastAPI entry point: validate the request and orchestrate the services."""

from fastapi import FastAPI, HTTPException

from src.models.schemas import ReviewRequest, ReviewResponse
from src.services.ai_agent import ReviewError, generate_review
from src.services.analyzer import run_static_analysis
from src.services.github import GitHubError, fetch_pr_files

app = FastAPI(title="AI Code Reviewer", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/review", response_model=ReviewResponse)
def review(request: ReviewRequest) -> ReviewResponse:
    """Run flake8 and mypy, then ask the LLM for a structured review.

    422 means invalid input, 404/413 come from the pull request lookup and 502
    means that GitHub or the LLM failed.
    """
    if request.code is not None:
        files = {"submitted.py": request.code}
    else:
        assert request.pr_url is not None  # guaranteed by ReviewRequest
        try:
            files = fetch_pr_files(request.pr_url)
        except GitHubError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    static = run_static_analysis(files)
    try:
        result = generate_review(files, static)
    except ReviewError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ReviewResponse(
        **result.model_dump(), files=list(files), static_analysis=static
    )
