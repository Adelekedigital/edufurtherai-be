# Development environment

## Database decision

Local development uses a dedicated local PostgreSQL database. The AI Router does not use the
existing Core or Scholarship Finder database, and local development does not require Supabase
Premium.

```text
Local development  -> local PostgreSQL
Staging             -> separate managed PostgreSQL project
Production          -> dedicated managed PostgreSQL project
```

The local database owns only AI Router data, including idempotency records, request outcomes,
usage, budget periods, provider attempts, routing policies, and service-token replay records.
It must not contain Core identity, scholarship source records, payment data, raw crawled HTML,
or unrestricted prompts and outputs.

Example local setting:

```env
DATABASE_URL=postgresql+asyncpg://router_user:router_password@localhost:5432/edufurther_ai_router
```

Plain PostgreSQL is sufficient. Docker Compose or the Supabase local stack are also acceptable;
the migration contract remains PostgreSQL-compatible. Production infrastructure choices remain
separate from this local setup.

## Next milestone

Add SQLAlchemy/Alembic persistence for idempotency, request outcomes, usage, budgets, routing
policies, and JWT replay protection. Migrations must be run explicitly; application startup must
not mutate the database.

```powershell
uv run alembic upgrade head
```
