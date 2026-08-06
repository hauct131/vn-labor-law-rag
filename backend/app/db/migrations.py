"""Small idempotent compatibility migrations for local managed deployments."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def _column_names(engine: Engine, table_name: str) -> set[str]:
    return {
        column["name"]
        for column in inspect(engine).get_columns(table_name)
    }


def apply_session_auth_compatibility(engine: Engine) -> None:
    """Extend an existing anonymous-user table before SQLAlchemy create_all."""

    inspector = inspect(engine)
    if "app_users" not in inspector.get_table_names():
        return

    columns = _column_names(engine, "app_users")
    dialect = engine.dialect.name
    timestamp_type = "TIMESTAMPTZ" if dialect == "postgresql" else "DATETIME"
    boolean_true = "TRUE" if dialect == "postgresql" else "1"

    statements: list[str] = []
    additions = {
        "email": "VARCHAR(320)",
        "password_hash": "VARCHAR(255)",
        "display_name": "VARCHAR(120)",
        "is_active": f"BOOLEAN NOT NULL DEFAULT {boolean_true}",
        "is_anonymous": f"BOOLEAN NOT NULL DEFAULT {boolean_true}",
        "updated_at": f"{timestamp_type}",
    }
    for name, definition in additions.items():
        if name not in columns:
            statements.append(
                f"ALTER TABLE app_users ADD COLUMN {name} {definition}"
            )

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text(
                "UPDATE app_users SET updated_at = created_at "
                "WHERE updated_at IS NULL"
            )
        )
        if dialect == "postgresql":
            connection.execute(
                text(
                    "ALTER TABLE app_users "
                    "ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE app_users "
                    "ALTER COLUMN updated_at SET NOT NULL"
                )
            )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_app_users_email ON app_users(email)"
            )
        )
