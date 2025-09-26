from alembic import op
import sqlalchemy as sa

revision = "20250926_0008"
down_revision = "20250925_0007"
branch_labels = None
depends_on = None

SCHEMA = "qc"

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'idx_items_line_station_detected'
                  AND n.nspname = '{SCHEMA}'
            ) THEN
                CREATE INDEX idx_items_line_station_detected
                    ON {SCHEMA}.items USING btree (line_id, station, detected_at DESC);
            END IF;
        END$$;
    """)

    op.execute(f"CREATE INDEX IF NOT EXISTS gin_items_product_code_trgm ON {SCHEMA}.items USING gin (product_code gin_trgm_ops)")
    op.execute(f"CREATE INDEX IF NOT EXISTS gin_items_roll_number_trgm  ON {SCHEMA}.items USING gin (roll_number gin_trgm_ops)")
    op.execute(f"CREATE INDEX IF NOT EXISTS gin_items_bundle_number_trgm ON {SCHEMA}.items USING gin (bundle_number gin_trgm_ops)")
    op.execute(f"CREATE INDEX IF NOT EXISTS gin_items_job_order_trgm   ON {SCHEMA}.items USING gin (job_order_number gin_trgm_ops)")
    op.execute(f"CREATE INDEX IF NOT EXISTS gin_items_roll_id_trgm     ON {SCHEMA}.items USING gin (roll_id gin_trgm_ops)")

    op.execute(f"CREATE INDEX IF NOT EXISTS idx_status_change_request_item_state ON {SCHEMA}.status_change_requests USING btree (item_id, state)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_item_images_item_id             ON {SCHEMA}.item_images           USING btree (item_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_item_defects_item_id            ON {SCHEMA}.item_defects          USING btree (item_id)")

    op.execute(f"CREATE INDEX IF NOT EXISTS idx_item_status_code ON {SCHEMA}.item_statuses USING btree (code)")

def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_item_status_code")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_item_defects_item_id")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_item_images_item_id")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_status_change_request_item_state")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.gin_items_roll_id_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.gin_items_job_order_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.gin_items_bundle_number_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.gin_items_roll_number_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.gin_items_product_code_trgm")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_items_line_station_detected")
