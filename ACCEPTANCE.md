# MVP acceptance record

Date: 2026-09-25. **All MVP gates passed.** Model: `gpt-4.1-mini` (temperature 0). Dataset: fresh seed from `db/init.sql` (500 customers, 20 products, 3,000 orders, 7,500 line items) in an isolated Compose project (`insightbot-acceptance3`, ports 18000/15432/16379) on Windows 11 with Docker 29.8.

| Gate | Result | Evidence |
|---|---|---|
| Python 3.12 setup | Passed | Python 3.12.13 venv from pinned `requirements-dev.txt` |
| Fast tests | Passed | 47 passed, 8 integration skipped without service URLs |
| Browser smoke | Passed (2026-09-22) | Headless Edge with scripted model and SQLite; not re-run this round |
| Fresh Compose startup | Passed | `docker compose -p insightbot-acceptance3 up --build --wait`: all three services healthy |
| PostgreSQL integration | Passed | 55 passed including all 8 integration tests (reader role write denial, timeout, seeded data) |
| Redis integration | Passed | Pair trimming, isolation, expiration, refresh, reconnection, deletion |
| API restart retains Redis history | Passed | Chat → `docker compose restart api` → `/sessions/{id}/history` returned the pair → DELETE 204 → 404 |
| English accuracy ≥8/10 | Passed: **10/10** | `reports/evaluation.json`; all Edmonton/Calgary follow-ups correct |
| Chinese accuracy ≥8/10 | Passed: **10/10** | Same report; all city follow-ups correct |
| Live progress <3 seconds | Passed | Request accepted: max 0.014 s. Median first tool 0.76 s, first answer token 2.7 s, total 3.4 s (max 5.9 s) |
| Write protection | Passed | 6 manual probes (DELETE, DROP, UPDATE, CTE DELETE injection, "system override" CREATE/pg_sleep, Chinese TRUNCATE). All refused; row counts and `orders.status` checksum unchanged. Guard and reader-role layers are covered by unit and integration tests |

Answers were checked against the reference rows by an automatic numeric comparison (CAD 0.01, 0.01 pp tolerance) and by reading every answer for filters, denominators and follow-up context. Formal human sign-off is recorded with `python -m evaluation.run --review`.

## Findings fixed during acceptance

1. **Integration test timing on Windows**: `localhost` tries IPv6 first and costs about 2 s per connection, which exceeded the Redis test's 2 s TTL. The test TTL is now 5 s, and the docs use `127.0.0.1`.
2. **LLM mental arithmetic**: gpt-4o-mini computed averages and percentage changes itself and was off by 0.02–0.8 (e.g. average order value 533.36 instead of 533.19). The prompt now requires every derived figure to come from a single SQL query, with no pasted literals.
3. **Correlated-subquery trap**: `customer_id IN (SELECT customer_id FROM customers …)` silently resolved to the outer `orders.customer_id` and matched every order. Foreign-key column comments were added to `db/init.sql`, and the prompt requires qualified columns with explicit joins.
4. **Join fan-out**: `COUNT(*)` after joining `order_items` counted line items. The prompt now requires `COUNT(DISTINCT orders.id)`.
5. **`aggregate` has no filter**: its docstring now tells the model to use `run_sql_query` for filtered figures.
6. **Model choice**: even after fixes 2–5, gpt-4o-mini failed a gate in 2 of 3 runs (the final division was still done in its head). gpt-4.1-mini scored 60/60 across three runs plus 20/20 on the official run, and was faster (median first token 2.7 s vs 3.4 s). It is now the default.

## Limitations

- Accuracy is measured on 10 fixed questions against one synthetic dataset; it does not prove accuracy on other questions.
- LLM output is non-deterministic even at temperature 0; the weekly evaluation workflow guards against regressions.
- "Last month"-style questions are relative to today's date and may return no data from the historical seed.

## Reproduce

```powershell
$env:API_PORT='18000'; $env:POSTGRES_PORT='15432'; $env:REDIS_PORT='16379'
docker compose -p insightbot-acceptance-new up --build --wait
$env:TEST_DATABASE_URL='postgresql+psycopg://insight_reader:reader_pw@127.0.0.1:15432/insightbot'
$env:TEST_REDIS_URL='redis://127.0.0.1:16379/0'
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-acceptance
$env:DATABASE_URL=$env:TEST_DATABASE_URL
python -m evaluation.run --base-url http://127.0.0.1:18000
python -m evaluation.run --review
```
