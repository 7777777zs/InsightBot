# MVP acceptance record

Date: 2026-09-22. Implementation is ready for external acceptance; the full MVP is not yet signed off.

| Gate | Result | Evidence / remaining work |
|---|---|---|
| Python 3.12 setup | Passed | Fresh workspace Python 3.12.13 virtual environment installed from pinned requirements-dev.txt |
| Fast tests | Passed | 47 passed; 8 integration tests skipped because service URLs are absent |
| Browser smoke | Passed | Headless Microsoft Edge, scripted model and SQLite: answers stream, SQL displays, follow-up requests, safe text, reset, mobile fit, SSE error recovery |
| PostgreSQL integration | Unverified | Docker unavailable; configure TEST_DATABASE_URL and run integration tests |
| Redis integration | Unverified | Configure TEST_REDIS_URL; tests cover pair trimming, isolation, expiration, refresh, reconnection and deletion |
| Fresh Compose startup | Unverified | Docker unavailable; README provides isolated project and ports |
| English accuracy >=8/10 | Unverified | No OpenAI key configured; run evaluation and human review |
| Chinese accuracy >=8/10 | Unverified | Same; all city follow-ups must also pass |
| Live progress <3 seconds | Unverified | Evaluation measures session receipt separately from first tool, first token and completion |
| API restart retains Redis history | Unverified | Redis reconnection test exists; confirm API restart against running Redis during acceptance |

## Reproduce verified tests

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-acceptance
```

The Python 3.12 test environment emits an upstream Starlette/AnyIO deprecation warning. It does not fail tests. The earlier system Python 3.14 run additionally emitted compatibility warnings; use the supported 3.12 environment.

For browser smoke, run `python -m tests.browser_demo` and then `python -m tests.browser_smoke` with Playwright and Microsoft Edge available. Screenshots are generated under ignored `.pytest-browser/`. The successful browser run used the system Python 3.14 Playwright installation and the scripted server; it does not establish real-model correctness.

## Complete external acceptance

1. Follow README isolated Compose startup and integration instructions. Do not reuse or delete existing project volumes.
2. Confirm `/ready`, schema and chat against the seeded database.
3. Make a chat request, restart only the API, and verify its history is still present. Clear it via the session API.
4. Run both languages with `python -m evaluation.run`, then `python -m evaluation.run --review`. Review every requested group/value and assumptions against independent reference SQL; SQL strings need not be identical.
5. Confirm >=8/10 for each language, all Edmonton/Calgary follow-ups correct, and every request-accepted event under three seconds. Record actual latency; do not substitute an immediate session event for answer-token latency.
6. Manually request DELETE/DROP/UPDATE and try prompt injection; verify reader-role protection and unchanged business data. The agent may decline directly or return a rejected tool attempt, but must never mutate data.
7. Record results here, including model, dataset, date, totals and limitations. Until then, pending gates remain unverified.
