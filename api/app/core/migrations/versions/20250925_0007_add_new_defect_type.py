from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250925_0007'
down_revision = '20250925_0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO qc.defect_types (code, name_th, display_order)
        VALUES ('SCRATCH','รอยขีด',50)
        ON CONFLICT (code) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM qc.defect_types
        WHERE code = 'SCRATCH';
    """)
