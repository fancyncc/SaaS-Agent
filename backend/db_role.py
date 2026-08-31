from __future__ import annotations

import sys

import psycopg
from psycopg import sql

from backend.config import get_settings


def owner_url() -> str:
    value = get_settings().migration_database_url or get_settings().database_url
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def ensure_role() -> None:
    settings = get_settings()
    with psycopg.connect(owner_url(), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (settings.app_db_user,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(settings.app_db_user), sql.Literal(settings.app_db_password)
            ))
        else:
            connection.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(settings.app_db_user), sql.Literal(settings.app_db_password)
            ))
        connection.execute(sql.SQL("ALTER ROLE {} NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
            sql.Identifier(settings.app_db_user)
        ))


def grant_privileges() -> None:
    settings = get_settings()
    with psycopg.connect(owner_url(), autocommit=True) as connection:
        database = connection.info.dbname
        role = sql.Identifier(settings.app_db_user)
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), role
        ))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        connection.execute(sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
        ).format(role))
        connection.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role))
        connection.execute(sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(role))
        connection.execute(sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(role))


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "ensure"
    if command == "ensure":
        ensure_role()
    elif command == "grant":
        grant_privileges()
    else:
        raise SystemExit(f"unknown command: {command}")
