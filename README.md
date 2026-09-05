# Edufurther AI Router

Independent FastAPI service owning approved AI task authorization, model/provider policy,
budgets, bounded fallback, schema validation and Langfuse telemetry. It is not a crawler,
publisher, eligibility engine, or general agent runtime.

## Contract

`POST /api/v1/internal/ai/execute` accepts only registered products and the V1 tasks
`scholarship_extraction` and `match_explanation`. Calls require a short-lived signed service
JWT and `Idempotency-Key`. Request payloads are limited to 16 KiB of source data. Responses
contain candidate output only; no `verified` state is ever produced.

## Run locally

```powershell
uv sync
uv run fastapi dev src/app/main.py
```

Copy `.env.example` to `.env` and provide the approved model(s), provider credentials consumed
by LiteLLM, and the service public key before making real calls. Provider and Langfuse choices
remain deployment decisions; this repository does not invent credentials or enable telemetry by
default.

Set `DATABASE_URL` to use the durable async PostgreSQL store. Run migrations explicitly before
starting the service; application startup never mutates the database:

```powershell
uv run alembic upgrade head
uv run fastapi dev src/app/main.py
```

When `DATABASE_URL` is unset, development tests use the in-process store. That mode is not
appropriate for multiple workers or production because its idempotency, replay and budget state
is not shared.

Generate local service JWT keys without OpenSSL:

```powershell
uv run python scripts/generate_service_keys.py
```

The generated `.local-secrets/` directory is ignored by Git. Only the public key belongs in the
Router configuration.
