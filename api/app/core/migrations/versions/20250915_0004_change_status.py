"""
seed base data: roles, users, lines, defect types (aligned with seed2)

Revision ID: 20250815_0003
Revises: 20250815_0002
Create Date: 2025-08-15 00:05:00
"""
from alembic import op

revision = '20250915_0004'
down_revision = '20250815_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL-safe, idempotent operations
    op.execute("""
      ALTER TYPE qc.review_type ADD VALUE IF NOT EXISTS 'REQUEST_STATUS_CHANGE';
      
      ALTER TABLE qc.reviews DROP COLUMN IF EXISTS defect_type_id;
    """)


def downgrade() -> None:
    op.execute("""
      DO $$
        BEGIN
            -- create a new type without REQUEST_STATUS_CHANGE
            CREATE TYPE qc.review_type_old AS ENUM ('DEFECT_FIX', 'SCRAP_FROM_RECHECK');

            -- alter columns to use old type
            ALTER TABLE qc.reviews ALTER COLUMN review_type TYPE qc.review_type_old
                USING review_type::text::qc.review_type_old;

            -- drop the new type
            DROP TYPE qc.review_type;

            -- rename back
            ALTER TYPE qc.review_type_old RENAME TO review_type;
      END$$;
        
      ALTER TABLE qc.reviews ADD COLUMN defect_type_id INT;
    """)
