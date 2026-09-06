# Scholarship Finder Integration Handoff

What the Scholarship Finder backend needs to prepare to call the AI Router. This is the
caller-side companion to [`docs/authentication.md`](authentication.md), which owns the full
authentication protocol, claim reference, Railway configuration, and key-rotation runbook — this
document doesn't repeat that detail, it walks through applying it for this specific integration.

## The trust boundary, in one line

The Router only ever verifies. It never generates, holds, or transmits a private key for anyone.
The Scholarship Finder backend owns its keypair end to end.

## One-time setup

1. **Generate a production RSA keypair** (RS256, 2048-bit minimum) with whatever tool your
   deployment already trusts. Store the private key in the Scholarship Finder backend's own
   secret store (its own Railway service variables, marked sensitive, or a dedicated secrets
   manager) — never in the Router's repo or Railway project, never committed to git.
2. **Fix these identifiers**, coordinated with whoever administers the Router's Railway
   `SERVICE_CALLERS` variable:
   - `subject`: `scholarship-finder-worker`
   - `issuer`: `scholarship-finder`
   - `audience`: `edufurther-ai-router`
   - `kid`: versioned, e.g. `scholarship-finder-2026`; bump on every rotation, never reuse
   - `scopes`: must include `ai:execute`
3. **Hand over only the public key PEM** plus the identifiers above to the Router admin. See
   [`docs/authentication.md#railway-configuration`](authentication.md#railway-configuration) for
   the exact `SERVICE_CALLERS` shape it gets registered into, and validate the JSON (including
   PEM escaping) before it's pasted into Railway — a caller entry with an empty `keys` map can
   never authenticate, and literal newlines in the PEM instead of `\n` escapes will crash the
   Router's deploy at startup.
4. **Get the Router's base URL** for each environment you call from (local/staging/production are
   separate deployments with separate `SERVICE_CALLERS` registrations).

## Per-request integration

1. **Sign a JWT in-process**, not by shelling out to the Router's CLI scripts (those are testing
   tools only). Required claims: `iss`, `sub`, `aud`, `iat`, `nbf`, `exp` (≤ 300 seconds from
   `iat`), `jti` (unique per call — reuse is permanently rejected), `scope`. Header: `alg: RS256`,
   `kid` set to your registered value.
2. **Call the endpoint**:
   ```
   POST /api/v1/internal/ai/execute
   Authorization: Bearer <jwt>
   Idempotency-Key: <must exactly match body.idempotency_key>
   ```
   Body: `product_id` (must be exactly `"scholarship_finder"`), `feature_id`, `task`
   (`scholarship_extraction` or `match_explanation` — nothing else is authorized), `taskrelation_id`
   (alias: `correlation_id`), `idempotency_key`, `source_data` (bounded to 16 KiB serialized),
   optional `journey_id` / `session_id` / `handoff_id`.
3. **Generate `idempotency_key` deterministically** per logical unit of work (e.g. a hash of the
   job/content), not randomly per attempt — a fresh random key on every retry defeats
   deduplication and can double-spend the shared budget.

## Handling the response

| Status | Meaning | What to do |
| --- | --- | --- |
| 200 | `ExecuteResponse` — check `status`: `completed`, `review`, `budget_exhausted`, or `provider_unavailable` | Only `completed` has a usable `output`; treat the others as "no result yet" |
| 401 | Bad/missing/replayed/wrong-key JWT (generic message by design) | Check key/claims/clock skew; don't retry blindly |
| 403 | `INSUFFICIENT_SCOPE` or `TASK_NOT_ALLOWED` | Config problem on your token or an unauthorized task — not retryable |
| 400 | `IDEMPOTENCY_KEY_MISMATCH` | Header didn't match body |
| 409 | `IDEMPOTENCY_CONFLICT` (same key, different payload) or `REQUEST_IN_PROGRESS` | Don't resend with a different body under the same key |
| 422 | `SOURCE_DATA_TOO_LARGE` or invalid task | Trim payload / fix task name |
| 429 | `RATE_LIMITED` | Back off and retry later |

## Never do this

- Send `output.verified` downstream as if the Router conferred it — it doesn't and never will
  (candidate output only, by design); your own review pipeline still owns verification.
- Store or transmit the private key anywhere near the Router's repo, Railway project, or a
  browser/client.
- Reuse a `jti`, or mint a token with a lifetime over 5 minutes.
- Register the same `kid` for two different key pairs — rotate to a new `kid` instead.
- Treat a local test keypair (generated for hand-testing the Router) as, or promote it into, a
  real caller's production registration.

## Getting a first test working

Mint a token, hit `/health` and `/ready` first (unauthenticated, confirms the Router itself is
reachable), then a real `/api/v1/internal/ai/execute` call against whichever environment's Router
you're pointed at. If you get a generic 401, confirm that environment's `SERVICE_CALLERS` actually
has your public key registered before assuming the code is broken — see
[`docs/authentication.md`](authentication.md) for the full diagnostic runbook.
