from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from adintel.core.settings import AppSettings
from adintel.db.models import Base


def build_engine(settings: AppSettings):
    return create_engine(settings.database_url, future=True)


def build_session_factory(settings: AppSettings) -> sessionmaker[Session]:
    engine = build_engine(settings)
    ensure_schema(engine, settings)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(settings: AppSettings) -> None:
    engine = build_engine(settings)
    ensure_schema(engine, settings, force=True)


def schema_file_path(settings: AppSettings) -> str:
    return str(settings.state_dir.parent / "sql" / "schema.sql")


def _schema_path(settings: AppSettings) -> Path:
    return Path(schema_file_path(settings))


def _schema_text(settings: AppSettings) -> str:
    path = _schema_path(settings)
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return path.read_text(encoding="utf-8")


def _schema_hash(schema_sql: str) -> str:
    return hashlib.sha256(schema_sql.encode("utf-8")).hexdigest()


def ensure_schema(engine, settings: AppSettings, *, force: bool = False) -> None:
    if not settings.auto_apply_schema and not force:
        return

    schema_sql = _schema_text(settings)
    current_hash = _schema_hash(schema_sql)
    timestamp_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "now()"

    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"""
            CREATE TABLE IF NOT EXISTS adintel_schema_state (
              schema_hash VARCHAR(64) PRIMARY KEY,
              schema_file TEXT NOT NULL,
              applied_at TIMESTAMPTZ DEFAULT {timestamp_default}
            )
            """
        )
        applied_hash = connection.exec_driver_sql(
            """
            SELECT schema_hash
            FROM adintel_schema_state
            ORDER BY applied_at DESC, schema_hash DESC
            LIMIT 1
            """
        ).scalar()

        if applied_hash == current_hash and not force:
            return

        raw_connection = connection.connection
        dialect_name = connection.dialect.name
        if dialect_name == "sqlite":
            raw_connection.executescript(schema_sql)
        else:
            with raw_connection.cursor() as cursor:
                cursor.execute(schema_sql)

        connection.exec_driver_sql("DELETE FROM adintel_schema_state")
        connection.execute(
            text(
                """
            INSERT INTO adintel_schema_state (schema_hash, schema_file)
            VALUES (:schema_hash, :schema_file)
            """
            ),
            {"schema_hash": current_hash, "schema_file": str(_schema_path(settings))},
        )
        Base.metadata.create_all(connection)
