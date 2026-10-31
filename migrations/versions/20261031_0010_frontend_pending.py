"""Categorias, retirada de produtos, planejamento por fornecedor e convites."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_0010"
down_revision = "20261023_0009"
branch_labels = None
depends_on = None


def _identity_columns():
    return (
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade():
    op.create_table(
        "category",
        *_identity_columns(),
        sa.Column(
            "demo_run_id", sa.Uuid(), sa.ForeignKey("demo_run.id"), nullable=False
        ),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.UniqueConstraint("demo_run_id", "code"),
    )
    op.execute("""
        INSERT INTO category(demo_run_id,code,name)
        SELECT id,'ALIMENTO_SECO_INDUSTRIALIZADO','Alimento seco industrializado' FROM demo_run
        ON CONFLICT (demo_run_id,code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO category(demo_run_id,code,name)
        SELECT DISTINCT demo_run_id,category,category FROM product
        WHERE demo_run_id IS NOT NULL AND category IS NOT NULL
        ON CONFLICT (demo_run_id,code) DO NOTHING
    """)
    op.drop_constraint(
        op.f("ck_product_publication_state_valid"), "product", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_product_publication_state_valid"),
        "product",
        "publication_state IN ('DRAFT','PUBLISHED','UNPUBLISHED')",
    )
    for table in ("vehicle", "route_plan_version"):
        op.add_column(
            table, sa.Column("supplier_organization_id", sa.Uuid(), nullable=True)
        )
        op.create_foreign_key(
            op.f(f"fk_{table}_supplier_organization_id_organization"),
            table,
            "organization",
            ["supplier_organization_id"],
            ["id"],
        )
        op.create_index(
            op.f(f"ix_{table}_supplier_organization_id"),
            table,
            ["supplier_organization_id"],
        )
    op.execute("""
        UPDATE route_plan_version p SET supplier_organization_id=w.supplier_organization_id
        FROM warehouse w WHERE w.id::text=p.snapshot->>'warehouse_id' AND w.demo_run_id=p.demo_run_id
    """)
    # Somente associações comprovadas por rotas; veículos sem dono inequívoco ficam privados.
    op.execute("""
        UPDATE vehicle v SET supplier_organization_id=owners.supplier_id
        FROM (
          SELECT a.vehicle_id, (array_agg(DISTINCT w.supplier_organization_id))[1] AS supplier_id
          FROM route_assignment a JOIN route r ON r.id=a.route_id
          JOIN warehouse w ON w.id=r.warehouse_id
          GROUP BY a.vehicle_id HAVING count(DISTINCT w.supplier_organization_id)=1
        ) owners WHERE v.id=owners.vehicle_id
    """)
    op.create_table(
        "user_invitation",
        *_identity_columns(),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organization.id"),
            nullable=False,
        ),
        sa.Column(
            "demo_run_id", sa.Uuid(), sa.ForeignKey("demo_run.id"), nullable=False
        ),
        sa.Column(
            "invited_by", sa.Uuid(), sa.ForeignKey("identity_user.id"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "role IN ('ROTALOG_ADMIN', 'ROUTE_PLANNER')", name="role_valid"
        ),
    )
    op.create_index(op.f("ix_user_invitation_email"), "user_invitation", ["email"])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM user_invitation)")
    ) or connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM product WHERE publication_state='UNPUBLISHED')"
        )
    ):
        raise RuntimeError(
            "Downgrade V5 bloqueado: preserve convites e produtos retirados."
        )
    # Categorias novas não podem ser apagadas silenciosamente.
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM category WHERE code <> 'ALIMENTO_SECO_INDUSTRIALIZADO')"
        )
    ):
        raise RuntimeError("Downgrade V5 bloqueado: preserve categorias cadastradas.")
    op.drop_table("user_invitation")
    for table in ("route_plan_version", "vehicle"):
        op.drop_index(op.f(f"ix_{table}_supplier_organization_id"), table_name=table)
        op.drop_constraint(
            op.f(f"fk_{table}_supplier_organization_id_organization"),
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "supplier_organization_id")
    op.drop_constraint(
        op.f("ck_product_publication_state_valid"), "product", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_product_publication_state_valid"),
        "product",
        "publication_state IN ('DRAFT','PUBLISHED')",
    )
    op.drop_table("category")
