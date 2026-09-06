# Edufurther AI Router

Independent FastAPI service owning approved AI task authorization, model/provider policy,
budgets, bounded fallback, schema validation and Langfuse telemetry. It is not a crawler,
publisher, eligibility engine, or general agent runtime.

## Contract

`POST /api/v1/internal/ai/execute` accepts only registered products and the V1 tasks
`scholarship_extraction` and `match_explanation`. Calls require a short-lived signed service
JWT and `Idempotency-Key`. Request payloads are limited to 16 KiB of source data. Responses
contain candidate output only; no `verified` state is ever produced.

## Authentication

The execute endpoint uses short-lived, signed service JWTs. It does not use user sessions or
password authentication. See [`docs/authentication.md`](docs/authentication.md) for the complete
caller configuration, token claims, Swagger workflow, and failure responses. Onboarding a new
caller service? Start with [`docs/integration-scholarship-finder.md`](docs/integration-scholarship-finder.md).

## Run locally

```powershell
uv sync
uv run fastapi dev
```

Railway start command:

```text
uv run fastapi run main.py --host 0.0.0.0 --port $PORT
```

The root `main.py` entrypoint adds `src/` to the import path. Do not use
`uvicorn src.app.main:app` or `uvicorn app.main:app` from the repository root. If Uvicorn is
needed instead of the FastAPI CLI, use `uv run uvicorn main:app --host 0.0.0.0 --port $PORT`.

Copy `.env.example` to `.env` and provide the approved model(s), provider credentials consumed
by LiteLLM, and the service public key before making real calls. Provider and Langfuse choices
remain deployment decisions; this repository does not invent credentials or enable telemetry by
default.

Set `DATABASE_URL` to use the durable async PostgreSQL store. Run migrations explicitly before
starting the service; application startup never mutates the database:

```powershell
uv run alembic upgrade head
uv run fastapi dev
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

Create a local caller assertion with the private key:

```powershell
uv run python scripts/create_service_token.py `
  --private-key .local-secrets/caller-private.pem `
  --kid scholarship-finder-2026 `
  --issuer scholarship-finder `
  --subject scholarship-finder-worker `
  --audience edufurther-ai-router
```

The command prints a short-lived JWT for the `Authorization: Bearer ...` header. The private key
never belongs in the Router or GitHub Actions migration secrets.

Database migrations are versioned in Git but run explicitly through the manual `Database migration`
GitHub Actions workflow. Configure `DATABASE_URL` as a secret on the selected GitHub environment;
the application does not migrate on startup.
