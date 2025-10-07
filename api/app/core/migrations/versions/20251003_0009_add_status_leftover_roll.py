from alembic import op
import sqlalchemy as sa

revision = "20251003_0009"
down_revision = "20250926_0008"
branch_labels = None
depends_on = None

SCHEMA = "qc"

def upgrade():
  op.execute("""
      INSERT INTO qc.item_statuses (code, name_th, display_order)
      VALUES ('LEFTOVER_ROLL','เศษม้วน',30)
      ON CONFLICT (code) DO NOTHING;
  """)

def downgrade():
  op.execute("""
      DELETE FROM qc.item_statuses
      WHERE code = 'LEFTOVER_ROLL';
  """)
