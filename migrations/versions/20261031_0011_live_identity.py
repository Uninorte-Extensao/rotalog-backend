"""Separa contextos operacionais e sintéticos sem reclassificar dados existentes."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_0011"
down_revision = "20261031_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "demo_run",
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default="true"),
    )
    for table in ("identity_user", "user_invitation"):
        op.add_column(
            table,
            sa.Column(
                "is_synthetic", sa.Boolean(), nullable=False, server_default="true"
            ),
        )


def downgrade():
    if any(
        op.get_bind().scalar(
            sa.text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE NOT is_synthetic)")
        )
        for table in ("demo_run", "identity_user", "user_invitation")
    ):
        raise RuntimeError(
            "Downgrade bloqueado: preserve os contextos e identidades operacionais."
        )
    for table in ("user_invitation", "identity_user"):
        op.drop_column(table, "is_synthetic")
    op.drop_column("demo_run", "is_synthetic")
