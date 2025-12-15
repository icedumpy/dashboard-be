from alembic import op

revision = "20251215_0011"
down_revision = "20251007_0010"
branch_labels = None
depends_on = None


def upgrade():
    # Create schema qc if it does not exist (safe if already created)
    op.execute("""
        CREATE SCHEMA IF NOT EXISTS qc;
    """)

    op.execute("""
      CREATE TABLE IF NOT EXISTS qc.cameras (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        channel_id   INTEGER NOT NULL,  -- logical channel, e.g. 3, 4
        line_id      BIGINT REFERENCES qc.production_lines(id)
                        ON UPDATE CASCADE
                        ON DELETE SET NULL,
        camera_name  TEXT NOT NULL,
        camera_ip    TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_cameras_channel UNIQUE (channel_id)
      );
        
      INSERT INTO "qc".cameras (channel_id, line_id, camera_name, camera_ip)
      VALUES
        (
          '4', 
          (SELECT id FROM qc.production_lines WHERE code='4'), 
          'Roll', 
          '192.168.10.108:554'
        ),
        (
          '3', 
          (SELECT id FROM qc.production_lines WHERE code='4'), 
          'Bundle', 
          '192.168.10.108:554'
        );
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS qc.cameras;
    """)
