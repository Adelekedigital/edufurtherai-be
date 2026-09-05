# Service Authentication

The router authenticates callers with short-lived RSA-signed JWTs. This is service-to-service
authentication: a registered product worker creates a JWT, sends it with the request, and the
router validates it before running an AI task.

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
JWT. The AI execute endpoint is protected in production.

## Required JWT Claims

Every token must contain:

| Claim | Meaning |
| --- | --- |
| `iss` | Caller issuer; must match the registered caller or global `SERVICE_ISSUER`. |
| `sub` | Caller subject; must match the registered caller. |
| `aud` | Caller audience; must match the registered caller or global `SERVICE_AUDIENCE`. |
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

The router can use one global public key registry or keys registered per caller. The recommended
production configuration registers each product caller explicitly:

```env
ENVIRONMENT=production
SERVICE_JWT_ALGORITHM=RS256
SERVICE_CALLERS={"scholarship_finder":{"subject":"scholarship-finder-worker","issuer":"scholarship-finder","audience":"edufurther-ai-router","keys":{"scholarship-finder-2026":"-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"},"scopes":["ai:execute"]}}
```

For a global key registry instead, use:

```env
SERVICE_JWT_KEYS={"scholarship-finder-2026":"-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"}
SERVICE_ISSUER=edufurther-ai-router
SERVICE_AUDIENCE=edufurther-ai-router
```

Never put a caller private key in Railway or in this repository. The private key stays with the
caller that creates tokens. The router stores only public keys.

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

## Key Rotation

1. Add the new public key under a new `kid` while keeping the old key temporarily.
2. Deploy the router configuration.
3. Update callers to sign with the new `kid`.
4. Remove the old public key after all old tokens have expired.

Because tokens expire within five minutes, old keys normally need to remain available for no more
than the token lifetime plus a small deployment buffer.
