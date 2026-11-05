# AI Code Reviewer

[![CI](https://github.com/BoSuY0/ai-code-reviewer/actions/workflows/ci_cd.yml/badge.svg)](https://github.com/BoSuY0/ai-code-reviewer/actions/workflows/ci_cd.yml)

A FastAPI microservice that reviews Python code. It runs flake8 and mypy over
the submitted code, passes their findings and the code to an LLM, and returns
one structured report with issues and refactoring suggestions.

The submitted code is only analyzed. It is never imported or executed.

## Architecture

```
src/
├── main.py              API layer: validation, orchestration, HTTP codes
├── models/schemas.py    Pydantic models: request, LLM review, response
└── services/
    ├── analyzer.py      flake8 + mypy in a temp dir, no shell, timeout
    ├── ai_agent.py      OpenRouter call, JSON schema output, retries
    └── github.py        fetches the .py files of a GitHub pull request
```

Request flow: `POST /review` → validate input → (for `pr_url`) download the
changed `.py` files from `api.github.com` → `run_static_analysis` →
`generate_review` → response.

| Status | Meaning |
|---|---|
| 200 | Review ready |
| 403 | Pull request is in a private repository |
| 404 | Pull request not found |
| 413 | Pull request is too large (more than 10 files or 20 000 characters) |
| 422 | Invalid input (empty or too long code, bad URL, both or neither field) |
| 502 | GitHub or the LLM failed (after retries) |

## Security

- Code is written to a fresh temporary directory and checked with
  `python -I -m flake8 --isolated` and `python -I -m mypy --strict
  --config-file=`; `shell=False`, a 30 s timeout per tool, and an environment
  without API keys. Isolated mode keeps the temp directory off `sys.path`, so a
  submitted `flake8.py` or `mypy.py` cannot replace the tool.
- Input is limited to 20 000 characters. File names must be relative `.py`
  paths without `.` or `..` segments and without segments that start with `-`;
  `--` ends the tool options, so a name is never read as a flag.
- `pr_url` must match `https://github.com/<owner>/<repo>/pull/<n>`. The service
  rebuilds the URL for `api.github.com` from these parts, so the user never
  chooses the host (SSRF protection).
- Only public repositories are reviewed (403 otherwise), so a `GITHUB_TOKEN`
  never sends private code to the LLM provider. Use a fine-grained token with
  public read-only access; it only raises the API rate limit.
- The code is wrapped in `<file>` tags and the system prompt tells the model to
  treat it as untrusted data. This lowers, but does not remove, the risk of
  prompt injection, so use the LLM verdict as advice, not as a merge gate.
- Keys come from environment variables. `.env` is ignored by Git and Docker.
- The Docker container runs as a non-root user with a read-only root file
  system, no Linux capabilities, and memory and process limits.

## Configuration

| Variable | Required | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | — |
| `LLM_MODEL` | no | `nvidia/nemotron-3.5-super-vl:free` |
| `GITHUB_TOKEN` | no | none (60 GitHub API requests per hour); public read-only |

```bash
cp .env.example .env   # then put your key into .env
```

## Run locally

Python 3.10–3.12.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn src.main:app --reload
```

## Run in Docker

```bash
docker compose up -d --build
curl -s 127.0.0.1:8000/health
```

## Example

```bash
curl -s -X POST 127.0.0.1:8000/review \
  -H 'Content-Type: application/json' \
  -d @samples/request.json
```

Response (shortened):

```json
{
  "summary": "Confirmed unused imports and missing type annotations. The file also contains a critical SQL injection, a division-by-zero bug, ...",
  "issues": [
    {
      "file": "submitted.py",
      "line": 7,
      "severity": "high",
      "message": "SQL injection via string concatenation",
      "suggestion": "Use parameterized query: cur.execute(\"SELECT * FROM users WHERE name = ?\", (name,))"
    }
  ],
  "score": 4,
  "files": ["submitted.py"],
  "static_analysis": {
    "findings": [
      {"tool": "flake8", "file": "submitted.py", "line": 1, "column": 1, "code": "F401", "message": "'os' imported but unused"},
      {"tool": "mypy", "file": "submitted.py", "line": 5, "column": 1, "code": "no-untyped-def", "message": "Function is missing a type annotation"}
    ],
    "errors": []
  }
}
```

A pull request is reviewed the same way:

```bash
curl -s -X POST 127.0.0.1:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"pr_url": "https://github.com/<owner>/<repo>/pull/<n>"}'
```

Interactive API docs are at `http://127.0.0.1:8000/docs`.

## Tests and quality gates

The LLM and GitHub are replaced with fakes in tests, so the suite needs no
network and no key.

```bash
black --check .
flake8 src tests
mypy --strict src tests
pytest --cov=src --cov-branch --cov-fail-under=90
```

`pre-commit install --install-hooks --hook-type pre-commit --hook-type commit-msg`
runs the same checks before each commit and enforces Conventional Commits.
GitHub Actions runs them on Python 3.10, 3.11 and 3.12 and then builds and
smoke-tests the Docker image (`.github/workflows/ci_cd.yml`). Merges into
`main` require all checks to pass. After a push to `main` the tested image is
saved as the `ai-code-reviewer-image-<sha>` artifact (7 days):

```bash
gh run download -n ai-code-reviewer-image-<sha>
gunzip -c ai-code-reviewer-image.tar.gz | docker load
```
