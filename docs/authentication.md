# Service Authentication

The router authenticates callers with short-lived RSA-signed JWTs. This is service-to-service
authentication: a registered product worker creates a JWT, sends it with the request, and the
router validates it before running an AI task.

This repository documents the authentication protocol; it does not issue credentials. Publishing
the protocol and token format is not a secret. A token can only be minted by a caller that possesses
the caller's private signing key. Keep that private key in the caller application's secret manager
or deployment secret store. Never put it in this repository, Railway Router variables, browser
code, Swagger, or a client application.

## Request Flow

1. The caller creates a new JWT with a unique `jti` and a lifetime of at most five minutes.
2. The caller sends the token in the HTTP header:

   ```http
   Authorization: Bearer <JWT>
   ```

3. The router reads the `kid` header and selects the matching public key.
4. The router verifies the signature, algorithm, required claims, time claims, issuer, subject,
   audience, and scopes.
5. The router stores the token's `iss` and `jti` in PostgreSQL. A token can only be accepted once.
6. The router checks that the product is authorized for the requested task.
7. Only then does the router call the configured model provider.

The `/health`, `/ready`, `/docs`, and `/openapi.json` endpoints are not protected by the service
JWT. The AI execute endpoint requires a JWT in every environment by default. For isolated local
development only, set `ALLOW_UNAUTHENTICATED_DEVELOPMENT=true`; never set this in Railway or any
shared environment.

## Required JWT Claims

Every token must contain:

| Claim | Meaning |
| --- | --- |
| `iss` | Caller issuer; must match the registered caller. |
| `sub` | Caller subject; must match the registered caller. |
| `aud` | Caller audience; must match the registered caller. |
| `iat` | Issued-at timestamp. |
| `nbf` | Not-before timestamp. |
| `exp` | Expiration timestamp. Maximum token lifetime is 300 seconds. |
| `jti` | Unique token ID. Reuse is rejected. |
| `scope` | Space-separated scopes, normally `ai:execute`. |

The JWT header must contain:

```json
{
  "alg": "RS256",
  "kid": "scholarship-finder-2026"
}
```

## Railway Configuration

Every caller must be registered explicitly with its own public key(s); there is no shared global
key registry. A caller with an empty `keys` map can never authenticate, by design:

```env
ENVIRONMENT=production
ALLOW_UNAUTHENTICATED_DEVELOPMENT=false
SERVICE_JWT_ALGORITHM=RS256
SERVICE_CALLERS={"scholarship_finder":{"subject":"scholarship-finder-worker","issuer":"scholarship-finder","audience":"edufurther-ai-router","keys":{"scholarship-finder-2026":"-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"},"scopes":["ai:execute"]}}
```

Never put a caller private key in Railway or in this repository. The private key stays with the
caller that creates tokens. The router stores only public keys.

Each application gets its own key pair and caller registration. For example, `scholarship_finder`
uses its own private key and `kid`; a future `career_matcher` application would use a different
private key, `kid`, issuer, subject, and caller entry. The router does not need an authentication
endpoint because it is a verifier, not a credential issuer. Token issuance happens inside the
calling application's trusted backend or an organization-managed token issuer.

## Create A Token Locally

Generate local service keys if needed:

```powershell
uv run python scripts/generate_service_keys.py
```

Create a five-minute token using the caller private key:

```powershell
uv run python scripts/create_service_token.py `
  --private-key .local-secrets/caller-private.pem `
  --kid scholarship-finder-2026 `
  --issuer scholarship-finder `
  --subject scholarship-finder-worker `
  --audience edufurther-ai-router `
  --scope ai:execute
```

The command prints a JWT. Each execution creates a new `jti`; do not reuse the same token for a
second request.

The `scope` argument must be a space-separated string. If the router is configured with
`SERVICE_JWT_REQUIRED_SCOPE=ai:execute`, the token must contain:

```json
{
  "scope": "ai:execute"
}
```

The registered caller's `scopes` list must also include `ai:execute`. A missing or differently named
scope, such as `execute` or `['ai:execute']`, results in `403 INSUFFICIENT_SCOPE` after the JWT
itself has been accepted. Use `--scope ai:execute` with the provided token utility.

## Swagger

1. Open `/docs`.
2. Click **Authorize**.
3. Paste the raw JWT only. Do not type the `Bearer ` prefix; Swagger adds it.
4. Click **Authorize**, then **Close**.
5. Execute `POST /api/v1/internal/ai/execute`.

Swagger sends:

```http
Authorization: Bearer <JWT>
```

The `Idempotency-Key` header must also match the request body's `idempotency_key` value.

## Failure Responses

| Status | Meaning |
| --- | --- |
| `401` | Missing, expired, malformed, replayed, incorrectly signed, or incorrectly identified JWT. |
| `403` | Valid identity but missing required scope or unauthorized product/task. |
| `400` | `Idempotency-Key` header does not match the body. |

Authentication failures intentionally return the same generic message and do not reveal whether a
key, claim, caller, or token ID was invalid.

`403 INSUFFICIENT_SCOPE` is the exception to that generic authentication response: it means the
JWT was valid enough to identify the caller, but the required scope was not present. Check both the
token's `scope` claim and `SERVICE_JWT_REQUIRED_SCOPE`/the registered caller's `scopes` setting.

## Key Rotation

1. Add the new public key under a new `kid` while keeping the old key temporarily.
2. Deploy the router configuration.
3. Update callers to sign with the new `kid`.
4. Remove the old public key after all old tokens have expired.

Because tokens expire within five minutes, old keys normally need to remain available for no more
than the token lifetime plus a small deployment buffer.

## Diagnosing And Fixing A 401 Caused By A Key Mismatch (Railway)

A 401 in a deployed environment almost always means the router's registered public key does not
match the private key the caller is actually signing with — not that the endpoint or the caller's
request is malformed.

### Confirm the router's registered key

1. Open the Railway project, select the Router service and environment, and open **Variables**.
2. Find `SERVICE_CALLERS` and locate the caller's `kid` (for `scholarship_finder`,
   `scholarship-finder-2026` by default).
3. Copy the PEM value registered under that `kid`.

### Confirm the caller's actual signing key

The caller's private key never lives in this repository or in the Router's Railway variables; it
lives in the calling service's own secret store. Ask that service (or its deployment config) for
the public key that corresponds to the private key it is signing with right now, and diff it
against the PEM from the step above. A mismatch — for example after the caller's key pair was
regenerated, rotated, or lost — is the entire bug; the router is behaving correctly by rejecting a
token signed with a key it does not recognize.

`.local-secrets/` in this repository is a local-dev convenience only, used to simulate being a
caller when exercising `POST /api/v1/internal/ai/execute` by hand. Running
`scripts/generate_service_keys.py` again overwrites it with no confirmation and no way to recover
the previous key pair; never treat it as the source of truth for a real deployed caller's identity.

### Roll the key without downtime

1. In Railway → Variables, edit `SERVICE_CALLERS` and add the new public key under a **new** `kid`
   (for example `scholarship-finder-2026-v2`) inside the existing caller's `keys` map, keeping the
   old `kid`/key pair in place. `SERVICE_CALLERS` is one JSON blob covering every registered
   caller — start from the current value rather than retyping it, so other callers are not dropped
   by mistake.
2. Save; Railway redeploys automatically. Confirm `/health` returns `200` after the redeploy.
3. Update the caller service to sign new tokens with the new `kid` and its matching private key.
4. Mint a token from the caller with the new `kid` and confirm
   `POST /api/v1/internal/ai/execute` returns something other than `401` (any other status, for
   example `provider_unavailable` when no model is configured, confirms authentication passed).
5. Once tokens signed with the old key have had time to expire (at most five minutes, the maximum
   JWT lifetime) and nothing is failing, remove the old `kid` entry from `SERVICE_CALLERS.keys` and
   redeploy again.

### Command-line alternative

```powershell
railway variables --set "SERVICE_CALLERS=<the full updated JSON>" --environment <environment-name>
```

Fetch the current value first (`railway variables` or `railway variables --json`) so the edit is
applied to the real current JSON rather than a stale copy. Flag names vary across CLI versions —
check `railway variables --help` for the installed version before running it against a shared
environment.
