"""""init schema (v2) reflecting scrap classification + simplified review flow

Revision ID: 20250815_0001
Revises:
Create Date: 2025-08-15 00:00:00
"""
from alembic import op

revision = '20250815_0001'
down_revision = None
branch_labels = None
depends_on = None

ddl = r"""
  -- ===== SCHEMAS =====
  CREATE SCHEMA IF NOT EXISTS "user";
  CREATE SCHEMA IF NOT EXISTS qc;

  -- ===== ENUMS =====
  DO $$ BEGIN
    CREATE TYPE "user".role AS ENUM ('OPERATOR','VIEWER','INSPECTOR');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;

  DO $$ BEGIN
    CREATE TYPE qc.station AS ENUM ('ROLL','BUNDLE');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;

  DO $$ BEGIN
    CREATE TYPE qc.review_type AS ENUM ('DEFECT_FIX','SCRAP_FROM_RECHECK');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;

  DO $$ BEGIN
    CREATE TYPE qc.review_state AS ENUM ('PENDING','APPROVED','REJECTED');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;

  DO $$ BEGIN
    CREATE TYPE qc.image_kind AS ENUM ('DETECTED','FIX','OTHER');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;

  -- ===== UTIL =====
  CREATE OR REPLACE FUNCTION qc.set_updated_at()
  RETURNS TRIGGER LANGUAGE plpgsql AS $$
  BEGIN
    NEW.updated_at := now();
    RETURN NEW;
  END $$;

  -- ===== MASTER: PRODUCTION LINES =====
  CREATE TABLE IF NOT EXISTS qc.production_lines (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code       TEXT UNIQUE NOT NULL,  -- '3','4'
    name       TEXT NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  DROP TRIGGER IF EXISTS trg_pl_updated ON qc.production_lines;
  CREATE TRIGGER trg_pl_updated
  BEFORE UPDATE ON qc.production_lines
  FOR EACH ROW EXECUTE FUNCTION qc.set_updated_at();

  -- ===== MASTER: SHIFTS =====
  CREATE TABLE IF NOT EXISTS qc.shifts (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code       TEXT UNIQUE NOT NULL,              -- e.g. A, B, C
    name       TEXT NOT NULL,                     -- e.g. Shift A (08:00-20:00)
    start_time TIME NOT NULL,
    end_time   TIME NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE
  );

  -- ===== USERS =====
  CREATE TABLE IF NOT EXISTS "user".users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL CHECK (username ~ '^[A-Za-z0-9_.@]+$'),
    display_name  TEXT NOT NULL,
    password      TEXT NOT NULL,
    role          "user".role NOT NULL,

    line_id       BIGINT REFERENCES qc.production_lines(id) ON UPDATE CASCADE ON DELETE SET NULL,
    shift_id      BIGINT REFERENCES qc.shifts(id)          ON UPDATE CASCADE ON DELETE SET NULL,

    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT users_line_shift_required_chk
      CHECK (
        role IN ('VIEWER','INSPECTOR')
        OR (line_id IS NOT NULL AND shift_id IS NOT NULL)
      )
  );
  DROP TRIGGER IF EXISTS trg_users_updated ON "user".users;
  CREATE TRIGGER trg_users_updated
  BEFORE UPDATE ON "user".users
  FOR EACH ROW EXECUTE FUNCTION qc.set_updated_at();

  CREATE INDEX IF NOT EXISTS idx_users_line  ON "user".users(line_id);
  CREATE INDEX IF NOT EXISTS idx_users_shift ON "user".users(shift_id);
  CREATE INDEX IF NOT EXISTS idx_users_role  ON "user".users(role);

  -- ===== DEFECT TYPES =====
  CREATE TABLE IF NOT EXISTS qc.defect_types (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          TEXT UNIQUE,
    name_th       TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    display_order INT NOT NULL DEFAULT 100,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  DROP TRIGGER IF EXISTS trg_defect_types_updated ON qc.defect_types;
  CREATE TRIGGER trg_defect_types_updated
  BEFORE UPDATE ON qc.defect_types
  FOR EACH ROW EXECUTE FUNCTION qc.set_updated_at();

  -- ===== ITEM STATUSES =====
  CREATE TABLE IF NOT EXISTS qc.item_statuses (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          TEXT UNIQUE NOT NULL,   -- DEFECT, SCRAP, RECHECK, NORMAL, QC_PASSED, REJECTED
    name_th       TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    display_order INT NOT NULL DEFAULT 100
  );

  -- ===== ITEMS =====
  DROP TABLE IF EXISTS qc.items CASCADE;

  CREATE TABLE qc.items (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station            qc.station NOT NULL,
    line_id            BIGINT NOT NULL REFERENCES qc.production_lines(id),

    product_code       TEXT,
    roll_number        TEXT,
    bundle_number      TEXT,
    job_order_number   TEXT,
    roll_width         NUMERIC(10,2),

    detected_at        TIMESTAMPTZ NOT NULL,
    item_status_id     BIGINT NOT NULL REFERENCES qc.item_statuses(id),
    ai_note            TEXT,

    scrap_requires_qc  BOOLEAN NOT NULL DEFAULT FALSE,
    scrap_confirmed_by BIGINT REFERENCES "user".users(id),
    scrap_confirmed_at TIMESTAMPTZ,

    current_review_id  BIGINT,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ,

    CONSTRAINT items_station_key_chk CHECK (
      (station='ROLL'   AND roll_number   IS NOT NULL AND bundle_number IS NULL)
      OR
      (station='BUNDLE' AND bundle_number IS NOT NULL)
    )
  );

  -- partial unique per kind
  CREATE UNIQUE INDEX IF NOT EXISTS uq_items_roll_number
    ON qc.items(roll_number) WHERE station='ROLL' AND deleted_at IS NULL;
  CREATE UNIQUE INDEX IF NOT EXISTS uq_items_bundle_number
    ON qc.items(bundle_number) WHERE station='BUNDLE' AND deleted_at IS NULL;

  -- query indexes
  CREATE INDEX IF NOT EXISTS idx_items_status_time ON qc.items(item_status_id, detected_at DESC);
  CREATE INDEX IF NOT EXISTS idx_items_line_time   ON qc.items(line_id, detected_at DESC);

  DROP TRIGGER IF EXISTS trg_items_updated ON qc.items;
  CREATE TRIGGER trg_items_updated
  BEFORE UPDATE ON qc.items
  FOR EACH ROW EXECUTE FUNCTION qc.set_updated_at();

  -- ===== ITEM ⇄ DEFECTS (M:N) =====
  CREATE TABLE IF NOT EXISTS qc.item_defects (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id         BIGINT NOT NULL REFERENCES qc.items(id) ON DELETE CASCADE,
    defect_type_id  BIGINT NOT NULL REFERENCES qc.defect_types(id) ON DELETE RESTRICT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta            JSONB
  );

  -- Prevent duplicate defect per item
  CREATE UNIQUE INDEX IF NOT EXISTS uq_item_defects_item_type
    ON qc.item_defects(item_id, defect_type_id);

  -- Query helpers
  CREATE INDEX IF NOT EXISTS ix_item_defects_item ON qc.item_defects(item_id);
  CREATE INDEX IF NOT EXISTS ix_item_defects_type ON qc.item_defects(defect_type_id);

  -- ===== BUNDLE <-> ROLLS mapping =====
  CREATE TABLE IF NOT EXISTS qc.bundle_rolls (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bundle_item_id   BIGINT NOT NULL REFERENCES qc.items(id) ON DELETE CASCADE,
    roll_item_id     BIGINT NOT NULL REFERENCES qc.items(id) ON DELETE RESTRICT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bundle_item_id, roll_item_id)
  );

  -- ===== REVIEWS =====
  CREATE TABLE IF NOT EXISTS qc.reviews (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id          BIGINT NOT NULL REFERENCES qc.items(id) ON DELETE CASCADE,
    review_type      qc.review_type NOT NULL,
    state            qc.review_state NOT NULL DEFAULT 'PENDING',

    submitted_by     BIGINT NOT NULL REFERENCES "user".users(id),
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    submit_note      TEXT,

    reviewed_by      BIGINT REFERENCES "user".users(id),
    reviewed_at      TIMESTAMPTZ,
    review_note      TEXT,

    defect_type_id   BIGINT REFERENCES qc.defect_types(id),
    reject_reason    TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  DROP TRIGGER IF EXISTS trg_reviews_updated ON qc.reviews;
  CREATE TRIGGER trg_reviews_updated
  BEFORE UPDATE ON qc.reviews
  FOR EACH ROW EXECUTE FUNCTION qc.set_updated_at();

  CREATE INDEX IF NOT EXISTS idx_reviews_pending
    ON qc.reviews(item_id) WHERE state='PENDING';

  CREATE INDEX IF NOT EXISTS idx_reviews_type_state_time
    ON qc.reviews(review_type, state, submitted_at DESC);

  -- ===== IMAGES =====
  CREATE TABLE IF NOT EXISTS qc.item_images (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id       BIGINT NOT NULL REFERENCES qc.items(id) ON DELETE CASCADE,
    review_id     BIGINT REFERENCES qc.reviews(id) ON DELETE CASCADE,
    kind          qc.image_kind NOT NULL,   -- DETECTED | FIX | OTHER
    path          TEXT NOT NULL,
    uploaded_by   BIGINT REFERENCES "user".users(id),
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta          JSONB
  );
  CREATE INDEX IF NOT EXISTS idx_item_images_item ON qc.item_images(item_id, uploaded_at DESC);

  -- ===== EVENT LOG =====
  CREATE TABLE IF NOT EXISTS qc.item_events (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id        BIGINT NOT NULL REFERENCES qc.items(id) ON DELETE CASCADE,
    actor_id       BIGINT REFERENCES "user".users(id),
    event_type     TEXT NOT NULL,
    from_status_id BIGINT REFERENCES qc.item_statuses(id),
    to_status_id   BIGINT REFERENCES qc.item_statuses(id),
    details        JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS idx_item_events_item_time
    ON qc.item_events(item_id, created_at DESC);
"""

def upgrade() -> None:
    op.execute(ddl)

def downgrade() -> None:
    op.execute('DROP SCHEMA IF EXISTS qc CASCADE;')
    op.execute('DROP SCHEMA IF EXISTS "user" CASCADE;')
