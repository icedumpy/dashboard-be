from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250925_0006'
down_revision = '20250916_0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename columns
    op.alter_column(
        'items',
        'scrap_confirmed_at',
        new_column_name='acknowledged_at',
        schema='qc',
    )
    op.alter_column(
        'items',
        'scrap_confirmed_by',
        new_column_name='acknowledged_by',
        schema='qc',
    )

    op.drop_column('items', 'scrap_requires_qc', schema='qc')


def downgrade() -> None:
    op.add_column(
        'items',
        sa.Column('scrap_requires_qc', sa.Boolean(), nullable=True),
        schema='qc',
    )

    op.alter_column(
        'items',
        'acknowledged_by',
        new_column_name='scrap_confirmed_by',
        schema='qc',
    )
    op.alter_column(
        'items',
        'acknowledged_at',
        new_column_name='scrap_confirmed_at',
        schema='qc',
    )
