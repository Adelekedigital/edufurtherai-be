from argparse import ArgumentParser
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    parser = ArgumentParser(description="Generate an RSA service JWT key pair")
    parser.add_argument("--output-dir", type=Path, default=Path(".local-secrets"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (args.output_dir / "caller-private.pem").write_bytes(private_bytes)
    (args.output_dir / "caller-public.pem").write_bytes(public_bytes)
    print(f"Wrote key pair to {args.output_dir.resolve()}")
    print(
        "Keep caller-private.pem with the calling service and configure only "
        "caller-public.pem on the Router."
    )


if __name__ == "__main__":
    main()
