"""AI Council v0.2.1 initial schema."""

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    db_dir = Path(__file__).parents[2] / "council" / "db"
    op.execute((db_dir / "schema.sql").read_text(encoding="utf-8"))
    op.execute((db_dir / "seeds.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("The audit-preserving MVP migration is intentionally irreversible")
