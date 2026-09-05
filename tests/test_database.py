from app.infra.database import normalize_database_url


def test_normalize_neon_libpq_url_for_asyncpg():
    url = normalize_database_url(
        "postgresql://user:secret@example.neon.tech/db"
        "?sslmode=require&channel_binding=require"
    )

    assert url == "postgresql+asyncpg://user:secret@example.neon.tech/db?ssl=require"


def test_normalize_preserves_asyncpg_urls():
    url = "postgresql+asyncpg://user:secret@example.neon.tech/db?ssl=require"

    assert normalize_database_url(url) == url
