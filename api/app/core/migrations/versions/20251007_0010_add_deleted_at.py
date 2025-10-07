from alembic import op
import sqlalchemy as sa

revision = "20251007_0010"
down_revision = "20251003_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE IF EXISTS qc.reviews
        ADD COLUMN IF NOT EXISTS deleted_at timestamptz NULL;
        COMMENT ON COLUMN qc.reviews.deleted_at IS 'Soft delete timestamp (UTC). NULL means active';

        ALTER TABLE IF EXISTS qc.status_change_requests
        ADD COLUMN IF NOT EXISTS deleted_at timestamptz NULL;
        COMMENT ON COLUMN qc.status_change_requests.deleted_at IS 'Soft delete timestamp (UTC). NULL means active';
        
        ALTER TABLE IF EXISTS qc.item_events
        ADD COLUMN IF NOT EXISTS deleted_at timestamptz NULL;
        COMMENT ON COLUMN qc.item_events.deleted_at IS 'Soft delete timestamp (UTC). NULL means active';
    """)


def downgrade():
    op.execute("""
        ALTER TABLE IF EXISTS qc.reviews
        DROP COLUMN IF EXISTS deleted_at;

        ALTER TABLE IF EXISTS qc.status_change_requests
        DROP COLUMN IF EXISTS deleted_at;
        
        ALTER TABLE IF EXISTS qc.item_events
        DROP COLUMN IF EXISTS deleted_at;
    """)
