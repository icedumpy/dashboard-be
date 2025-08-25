"""
seed base data: roles, users, lines, defect types (aligned with seed2)

Revision ID: 20250815_0002
Revises: 20250815_0001
Create Date: 2025-08-15 00:05:00
"""
from alembic import op

revision = '20250815_0002'
down_revision = '20250815_0001'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
      -- ===== SEED: lines, shifts, users =====
      
      CREATE EXTENSION IF NOT EXISTS pgcrypto;

      -- Lines
      INSERT INTO qc.production_lines (code, name, is_active)
      VALUES
        ('3','Production Line 3', TRUE),
        ('4','Production Line 4', TRUE)
      ON CONFLICT (code) DO NOTHING;

      -- Shifts (lean master)
      INSERT INTO qc.shifts (code, name, start_time, end_time, is_active) VALUES
        ('A','Shift A (08:00-20:00)','08:00','20:00', TRUE),
        ('B','Shift B (20:00-08:00)','20:00','08:00', TRUE),
        ('C','Shift C','00:00','08:00', TRUE),
        ('D','Shift D','16:00','00:00', TRUE)
      ON CONFLICT (code) DO NOTHING;

      -- Helper CTEs for FKs
      WITH
        l3 AS (SELECT id FROM qc.production_lines WHERE code='3'),
        l4 AS (SELECT id FROM qc.production_lines WHERE code='4'),
        sA AS (SELECT id FROM qc.shifts WHERE code='A'),
        sB AS (SELECT id FROM qc.shifts WHERE code='B'),
        sC AS (SELECT id FROM qc.shifts WHERE code='C'),
        sD AS (SELECT id FROM qc.shifts WHERE code='D')

      -- Operators + Inspectors + Viewer
      INSERT INTO "user".users (username, display_name, password, role, line_id, shift_id, is_active)
      VALUES
        ('OPTH03A','Operator L3A', crypt('133Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sA), TRUE),
        ('OPTH03B','Operator L3B', crypt('134Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sB), TRUE),
        ('OPTH03C','Operator L3C', crypt('130Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sC), TRUE),
        ('OPTH03D','Operator L3D', crypt('135Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sD), TRUE),
        ('OPTH04A','Operator L4A', crypt('143Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sA), TRUE),
        ('OPTH04B','Operator L4B', crypt('144Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sB), TRUE),
        ('OPTH04C','Operator L4C', crypt('140Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sC), TRUE),
        ('OPTH04D','Operator L4D', crypt('145Abc###', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sD), TRUE),
        ('QATH03A','QC L3A'      , crypt('456Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sA), TRUE),
        ('QATH03B','QC L3B'      , crypt('457Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sB), TRUE),
        ('QATH03C','QC L3C'      , crypt('458Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sC), TRUE),
        ('QATH03D','QC L3D'      , crypt('459Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sD), TRUE),
        ('QATH04A','QC L4A'      , crypt('460Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sA), TRUE),
        ('QATH04B','QC L4B'      , crypt('461Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sB), TRUE),
        ('QATH04C','QC L4C'      , crypt('462Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sC), TRUE),
        ('QATH04D','QC L4D'      , crypt('463Abc###', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sD), TRUE),
        ('Fitesacnc2','Viewer'   , crypt('FCNC2###', gen_salt('bf', 12)),'VIEWER',NULL,NULL, TRUE)
      ON CONFLICT (username) DO NOTHING;


      -- Item statuses
      INSERT INTO qc.item_statuses (code, name_th, display_order) VALUES
        ('DEFECT',    'Defect',      10),
        ('REJECTED',  'Rejected',    20),
        ('SCRAP',     'Scrap',       30),
        ('RECHECK',   'Recheck',     40),
        ('NORMAL',    'Normal',      50),
        ('QC_PASSED', 'QC Passed',   60)
      ON CONFLICT (code) DO NOTHING;

      -- Defect types
      INSERT INTO qc.defect_types (code, name_th, display_order) VALUES
        ('LABEL','ฉลาก',10),
        ('BARCODE','บาร์โค้ด',20),
        ('TOP','ด้านบน',30),
        ('BOTTOM','ด้านล่าง',40)
      ON CONFLICT (code) DO NOTHING;
      """)

def downgrade() -> None:
    op.execute("""
      -- ลบตัวอย่างข้อมูล
      DELETE FROM qc.item_events
      WHERE details::text LIKE '%AI scrap confirmed%';

      DELETE FROM qc.item_defects
      WHERE meta::text LIKE '%AI%' OR meta::text LIKE '%missing label%';

      DELETE FROM qc.items
      WHERE roll_number IN ('462263','451070')
         OR bundle_number IN ('461182');

      -- ลบ defect types
      DELETE FROM qc.defect_types
      WHERE code IN ('LABEL','BARCODE','TOP','BOTTOM');

      -- ลบ item statuses
      DELETE FROM qc.item_statuses
      WHERE code IN ('DEFECT','REJECTED','SCRAP','RECHECK','NORMAL','QC_PASSED');

      -- ลบ users
      DELETE FROM "user".users
      WHERE username IN ('op_3a','op_3b','op_3c','op_3d',
                         'op_4a','op_4b','op_4c','op_4d',
                         'qc_3a','qc_3b','qc_3c','qc_3d',
                         'qc_4a','qc_4b','qc_4c','qc_4d',
                         'viewer');

      -- ลบ production lines
      DELETE FROM qc.production_lines
      WHERE code IN ('3','4');
    """)
