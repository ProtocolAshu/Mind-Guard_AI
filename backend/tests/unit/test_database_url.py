"""Connection strings that managed PostgreSQL providers hand out must work unchanged."""

from app.database.session import normalize_database_url


def test_provider_urls_are_normalized_for_asyncpg():
    assert normalize_database_url("postgres://u:p@host/db") == "postgresql+asyncpg://u:p@host/db"
    assert normalize_database_url("postgresql://u:p@host/db") == "postgresql+asyncpg://u:p@host/db"
    # libpq's sslmode becomes asyncpg's ssl (Neon, Supabase and Render all send sslmode=require)
    assert normalize_database_url("postgres://u:p@host/db?sslmode=require") == "postgresql+asyncpg://u:p@host/db?ssl=require"


def test_libpq_only_parameters_are_dropped():
    url = normalize_database_url("postgres://u:p@host/db?sslmode=require&channel_binding=require&application_name=x")
    assert url == "postgresql+asyncpg://u:p@host/db?ssl=require"


def test_already_correct_and_sqlite_urls_pass_through():
    assert normalize_database_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_database_url("postgresql+asyncpg://u:p@h/db?ssl=require") == "postgresql+asyncpg://u:p@h/db?ssl=require"
    assert normalize_database_url("sqlite+aiosqlite:///./mindguard.db") == "sqlite+aiosqlite:///./mindguard.db"
    assert normalize_database_url("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"
