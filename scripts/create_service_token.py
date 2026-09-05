from argparse import ArgumentParser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt


def main() -> None:
    parser = ArgumentParser(description="Create a short-lived service JWT assertion")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--kid", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--scope", default="ai:execute")
    parser.add_argument("--ttl-seconds", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.ttl_seconds <= 300:
        parser.error("--ttl-seconds must be between 1 and 300")

    now = datetime.now(UTC)
    claims = {
        "iss": args.issuer,
        "sub": args.subject,
        "aud": args.audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=args.ttl_seconds),
        "jti": uuid4().hex,
        "scope": args.scope,
    }
    token = jwt.encode(
        claims,
        args.private_key.read_bytes(),
        algorithm="RS256",
        headers={"kid": args.kid},
    )
    print(token)


if __name__ == "__main__":
    main()
