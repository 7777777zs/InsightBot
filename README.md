# InsightBot

Ask business questions in English or Chinese, inspect the SQL behind each answer, and continue the conversation. FastAPI + LangChain/OpenAI + PostgreSQL + Redis. This is a local demo with sample Canadian e-commerce data, not a production service.

## Start with Docker

Prerequisites: Docker with Compose and a valid OpenAI API key. Copy `.env.example` to `.env` and replace `OPENAI_API_KEY`. Never commit `.env`.

```powershell
Copy-Item .env.example .env
# Edit .env, then:
docker compose up --build --wait
```

Open http://localhost:8000 for the demo, `/docs` for API documentation, `/health` for liveness, or `/ready` for PostgreSQL/Redis readiness. OpenAI credentials are checked for presence at startup; provider connectivity is verified by an actual chat. Services bind to localhost only.

The seed contains 500 customers, 20 products, 3,000 orders and their line items. Initialization runs only on a new Postgres volume. Existing data is never reseeded automatically. Credentials in Compose are local demo credentials.

## Local development and tests

Python 3.12 is the supported target (also specified in `.python-version` and Dockerfile).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
docker compose up -d postgres redis
python -m uvicorn app.main:app --reload
python -m pytest -q
```

Direct dependency versions are pinned and verified in a fresh Python 3.12.13 virtual environment; `constraints.txt` records the full tested dependency resolution. Docker verification remains pending in this environment. See `ACCEPTANCE.md`. Runtime requirements exclude pytest; the evaluation runner uses httpx from development requirements. If the default pytest temp directory is inaccessible, pass `--basetemp .pytest-local` (use this dedicated test directory only).

`REDIS_URL=memory://` enables ephemeral in-process history. It is intended for tests/development and does not survive restart. Configuration also accepts positive `MAX_ROWS`, `STATEMENT_TIMEOUT_MS`, `HISTORY_TTL_SECONDS`, `AGENT_MAX_STEPS`, and an even `HISTORY_MAX_MESSAGES` of at least 2. Defaults are 200 rows, 5 seconds, 24 hours, 10 steps and 20 messages.

## API

- `POST /chat`: `{ "question": "How many customers?", "session_id": "optional-session" }` → `{session_id, answer, sql}`.
- `POST /chat/stream`: same input; SSE `data: {json}` frames. Event types: `session`, `tool_call`, `tool_result`, `token`, then `done` or `error`.
- `GET /sessions/{id}/history`: completed user/assistant messages; unknown sessions return 404.
- `DELETE /sessions/{id}`: clear history, returns 204.
- `GET /schema`: table names and columns.
- `GET /health`: process liveness; `GET /ready`: 200 when database and history respond, otherwise 503.

Questions must contain non-whitespace text and be at most 2,000 characters. Session IDs use 1–128 letters, digits, underscores or hyphens. The browser keeps its session ID in local storage; reload retains context but does not redraw previous messages. New conversation clears the server history.

The first SSE frame confirms receipt, not answer generation. The demo uses POST streaming fetch, displays tool progress, and replaces streamed draft text with the final answer. Disconnected or unsuccessful generation does not save partial answers. Redis expiration refreshes after successful turns. Requests using the same session should be sent sequentially.

Dependency failures return 503, provider failures 502, agent step exhaustion 422, and unexpected chat failures a generic 500. Errors after streaming starts use an `error` event. Tool-level SQL errors remain visible to the agent so it can retry. There is no automatic fallback from Redis to memory.

## Isolated integration acceptance

Use a separate Compose project and ports to avoid touching an existing database. These commands are PowerShell; use a new project name for each fresh-seed run.

```powershell
$env:API_PORT='18000'
$env:POSTGRES_PORT='15432'
$env:REDIS_PORT='16379'
docker compose -p insightbot-acceptance up --build --wait
$env:TEST_DATABASE_URL='postgresql+psycopg://insight_reader:reader_pw@localhost:15432/insightbot'
$env:TEST_REDIS_URL='redis://localhost:16379/0'
python -m pytest -m integration -q
```

Integration tests skip when service URLs are absent, and fail if configured services are unreachable. PostgreSQL tests require the seeded demo database and reader role. Write-denial probes are rolled back even if the role is misconfigured. Redis tests use unique keys and clean up only those keys. `docker compose -p insightbot-acceptance down` stops acceptance services while preserving its volume; avoid `down -v` unless intentionally discarding that project's data.

## Bilingual accuracy evaluation

With the acceptance API running and development dependencies installed:

```powershell
$env:DATABASE_URL=$env:TEST_DATABASE_URL
python -m evaluation.run --base-url http://localhost:18000
python -m evaluation.run --review
```

The runner executes 10 English and 10 Chinese questions, with separate sessions except the Edmonton → Calgary → percentage comparison chain. Reference SQL runs against the same seeded database. Ensure the evaluator database URL and API database point to the same dataset. The report records answers, SQL, reference rows, request-acceptance time, first tool time, first token time and total duration. Model metadata comes from the evaluator's `OPENAI_MODEL`; keep it aligned with the API.

Review checks numbers against reference rows, monetary rounding within CAD 0.01, percentages within 0.01 percentage points, and correct assumptions/context. Accuracy is pending until explicitly reviewed. Acceptance requires at least 8/10 per language, all three city questions correct, no request errors, and request acceptance under 3 seconds for every evaluated request. First answer-token latency is measured separately and has no 3-second guarantee.

Business definitions: revenue and units sold count completed orders; revenue uses historical line-item prices. Average order value divides completed revenue by completed order count. Refund rate is refunded orders / all orders. City comparisons use Edmonton as the baseline, `(Calgary - Edmonton) / Edmonton * 100`; a zero denominator yields undefined rather than an invented percentage.

## Troubleshooting and remaining boundaries

- Missing API key: populate `.env` before starting. Invalid keys are reported as provider failures on chat.
- PostgreSQL connection failure: check ports, reader credentials and whether the seed initialized a fresh volume.
- Redis unavailable: `/ready` returns 503; restore Redis and retry.
- Historical dataset: ask explicit dates such as 2024; “last month” is relative to the current date and may have no data.
- Tests use a scripted model and SQLite by default; passing them does not establish live-model accuracy or PostgreSQL safety.

The MVP excludes authentication, uploads, charts, multi-tenancy and production deployment. See `ACCEPTANCE.md` for the actual verification state.

## Optional browser smoke test

Install Playwright in your development environment (`python -m pip install playwright`) and have Microsoft Edge installed. In one terminal run `python -m tests.browser_demo`; in another run `python -m tests.browser_smoke`. This uses a scripted model and SQLite, requires no API key, and covers streaming, SQL display, follow-ups, safe text rendering, reset, mobile layout and SSE error recovery. It is a fixture, not a live accuracy test.
