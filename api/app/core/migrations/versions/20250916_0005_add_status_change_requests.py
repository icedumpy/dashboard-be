# revision identifiers, used by Alembic.
revision = "20250916_0005"
down_revision = "20250915_0004"
branch_labels = None
depends_on = None

from alembic import op

def upgrade():
    op.execute("""
      CREATE TABLE IF NOT EXISTS qc.status_change_requests (
          id BIGSERIAL PRIMARY KEY,
          item_id BIGINT NOT NULL
              REFERENCES qc.items(id) ON DELETE CASCADE,
          from_status_id INT NOT NULL
              REFERENCES qc.item_statuses(id),
          to_status_id INT NOT NULL
              REFERENCES qc.item_statuses(id),
          state qc.review_state NOT NULL DEFAULT 'PENDING',
          requested_by INT NOT NULL
              REFERENCES "user".users(id),
          requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          approved_by INT NULL
              REFERENCES "user".users(id),
          approved_at TIMESTAMPTZ NULL,
          reason TEXT NULL,
          meta JSONB NULL
      );

      CREATE INDEX IF NOT EXISTS idx_scr_item_id
          ON qc.status_change_requests(item_id);

      CREATE INDEX IF NOT EXISTS idx_scr_state
          ON qc.status_change_requests(state);

      -- child table for defects linked to a request
      CREATE TABLE IF NOT EXISTS qc.status_change_request_defects (
          request_id BIGINT NOT NULL
              REFERENCES qc.status_change_requests(id) ON DELETE CASCADE,
          defect_type_id INT NOT NULL
              REFERENCES qc.defect_types(id),
          PRIMARY KEY (request_id, defect_type_id)
      );

      CREATE INDEX IF NOT EXISTS idx_scrd_defect_type
          ON qc.status_change_request_defects(defect_type_id);
    """)


def downgrade():
    op.execute("""
    DROP INDEX IF EXISTS qc.idx_scrd_defect_type;
    DROP TABLE IF EXISTS qc.status_change_request_defects;

    DROP INDEX IF EXISTS qc.idx_scr_state;
    DROP INDEX IF EXISTS qc.idx_scr_item_id;
    DROP TABLE IF EXISTS qc.status_change_requests;
    """)
