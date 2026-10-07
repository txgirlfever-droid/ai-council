"""AI Council v0.2.1 initial schema."""

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _execute_script(sql: str) -> None:
    """Run a multi-statement SQL script.

    asyncpg turns ordinary statements into prepared statements, which reject
    scripts containing several commands. The driver's own ``execute`` uses the
    simple-query protocol instead, so the whole file runs in one call inside
    Alembic's migration transaction.
    """
    adapted = op.get_bind().connection.dbapi_connection
    adapted.run_async(lambda driver: driver.execute(sql))


def upgrade() -> None:
    db_dir = Path(__file__).parents[2] / "council" / "db"
    _execute_script((db_dir / "schema.sql").read_text(encoding="utf-8"))
    _execute_script((db_dir / "seeds.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("The audit-preserving MVP migration is intentionally irreversible")
