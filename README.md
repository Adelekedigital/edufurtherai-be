# Edufurther AI Router

Independent FastAPI service owning approved AI task authorization, model/provider policy,
budgets, bounded fallback, schema validation and Langfuse telemetry. It is not a crawler,
publisher, eligibility engine, or general agent runtime.

## Contract

`POST /api/v1/internal/ai/execute` accepts only registered products and registered tasks.
Calls require a short-lived signed service JWT and `Idempotency-Key`. Responses contain
candidate output only; no `verified` state is ever produced.

Tasks are authorized per product - sharing this router does not mean sharing a task surface.

| Product | Tasks |
|---|---|
| `scholarship_finder` | `scholarship_extraction`, `match_explanation` |
| `edufurther_agent` | `classify_source_page`, `split_list_candidates`, `extract_scholarship_facts`, `compare_official_evidence`, `extract_eligibility_requirements` |

Each task declares its own source-payload ceiling in `POLICIES`
([`src/app/domain/ai_router.py`](src/app/domain/ai_router.py)), measured against the JSON the
provider actually receives. They differ by orders of magnitude - classifying a page needs a
sample, splitting a list page needs the whole thing - so there is no single global limit.

Every response carries `prompt_version` alongside `model_policy_version`. Products persist it
with the facts they extract, so an accuracy regression can be traced back to the exact prompt
that produced it. Change a prompt's text, bump its version.

Adding a task means four edits, all in this service: the `Task` enum, its `POLICIES` entry,
its validator, and its `SYSTEM_PROMPTS` entry. `tests/test_task_registry.py` fails loudly if
any of the four is missed - without it a missing prompt surfaces at runtime as a misleading
`provider_unavailable`.

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
