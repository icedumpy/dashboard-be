"""
seed base data: roles, users, lines, defect types (aligned with seed2)

Revision ID: 20250815_0003
Revises: 20250815_0002
Create Date: 2025-08-15 00:05:00
"""
from alembic import op

revision = '20250815_0003'
down_revision = '20250815_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL-safe, idempotent operations
    op.execute("""
        -- 1) Drop the check constraint if it exists
        ALTER TABLE qc.items
        DROP CONSTRAINT IF EXISTS items_station_key_chk;

        -- 2) Add roll_id column only if not exists
        ALTER TABLE qc.items
        ADD COLUMN IF NOT EXISTS roll_id TEXT;

        -- 3) Drop unique indexes if they exist
        DROP INDEX IF EXISTS qc.uq_items_bundle_number;
        DROP INDEX IF EXISTS qc.uq_items_roll_number;
    """)


def downgrade() -> None:
    op.execute("""
        -- 1) Drop the added column if it exists
        ALTER TABLE qc.items
        DROP COLUMN IF EXISTS roll_id;

        -- 2) Recreate the check constraint IF NOT ALREADY PRESENT.
        --    Enforces that exactly one of (roll_number, bundle_number) is set
        --    according to station type.
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.conname = 'items_station_key_chk'
              AND n.nspname = 'qc'
              AND t.relname = 'items'
          ) THEN
            ALTER TABLE qc.items
            ADD CONSTRAINT items_station_key_chk
            CHECK (
              (station = 'ROLL'::qc.station AND roll_number IS NOT NULL AND bundle_number IS NULL)
              OR
              (station = 'BUNDLE'::qc.station AND bundle_number IS NOT NULL AND roll_number IS NULL)
            );
          END IF;
        END
        $$;

        -- 3) Recreate the unique indexes if they don't exist.
        -- Try to add partial indexes that ignore soft-deleted rows (deleted_at IS NULL)
        -- when the column exists; otherwise create plain unique indexes.

        DO $$
        DECLARE
          has_deleted_at boolean;
        BEGIN
          SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema='qc' AND table_name='items' AND column_name='deleted_at'
          ) INTO has_deleted_at;

          -- uq_items_roll_number
          IF NOT EXISTS (
            SELECT 1
            FROM pg_class i
            JOIN pg_namespace ns ON ns.oid = i.relnamespace
            WHERE i.relkind = 'i'
              AND i.relname = 'uq_items_roll_number'
              AND ns.nspname = 'qc'
          ) THEN
            IF has_deleted_at THEN
              EXECUTE $sql$
                CREATE UNIQUE INDEX IF NOT EXISTS uq_items_roll_number
                ON qc.items (roll_number)
                WHERE station = 'ROLL'::qc.station AND deleted_at IS NULL
              $sql$;
            ELSE
              EXECUTE $sql$
                CREATE UNIQUE INDEX IF NOT EXISTS uq_items_roll_number
                ON qc.items (roll_number)
                WHERE station = 'ROLL'::qc.station
              $sql$;
            END IF;
          END IF;

          -- uq_items_bundle_number
          IF NOT EXISTS (
            SELECT 1
            FROM pg_class i
            JOIN pg_namespace ns ON ns.oid = i.relnamespace
            WHERE i.relkind = 'i'
              AND i.relname = 'uq_items_bundle_number'
              AND ns.nspname = 'qc'
          ) THEN
            IF has_deleted_at THEN
              EXECUTE $sql$
                CREATE UNIQUE INDEX IF NOT EXISTS uq_items_bundle_number
                ON qc.items (bundle_number)
                WHERE station = 'BUNDLE'::qc.station AND deleted_at IS NULL
              $sql$;
            ELSE
              EXECUTE $sql$
                CREATE UNIQUE INDEX IF NOT EXISTS uq_items_bundle_number
                ON qc.items (bundle_number)
                WHERE station = 'BUNDLE'::qc.station
              $sql$;
            END IF;
          END IF;

        END
        $$;
    """)
