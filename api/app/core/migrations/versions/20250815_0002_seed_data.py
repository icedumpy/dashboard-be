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
        ('op_3a','Operator L3A', crypt('op_3a', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sA), TRUE),
        ('op_3b','Operator L3B', crypt('op_3b', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sB), TRUE),
        ('op_3c','Operator L3C', crypt('op_3c', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sC), TRUE),
        ('op_3d','Operator L3D', crypt('op_3d', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sD), TRUE),
        ('op_4a','Operator L4A', crypt('op_4a', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sA), TRUE),
        ('op_4b','Operator L4B', crypt('op_4b', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sB), TRUE),
        ('op_4c','Operator L4C', crypt('op_4c', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sC), TRUE),
        ('op_4d','Operator L4D', crypt('op_4d', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sD), TRUE),
        ('qc_3a','QC L3A', crypt('qc_3a', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sA), TRUE),
        ('qc_3b','QC L3B', crypt('qc_3b', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sB), TRUE),
        ('qc_3c','QC L3C', crypt('qc_3c', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sC), TRUE),
        ('qc_3d','QC L3D', crypt('qc_3d', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sD), TRUE),
        ('qc_4a','QC L4A', crypt('qc_4a', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sA), TRUE),
        ('qc_4b','QC L4B', crypt('qc_4b', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sB), TRUE),
        ('qc_4c','QC L4C', crypt('qc_4c', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sC), TRUE),
        ('qc_4d','QC L4D', crypt('qc_4d', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sD), TRUE),
        ('viewer','Viewer', crypt('viewer', gen_salt('bf', 12)),'VIEWER',NULL,NULL, TRUE)
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
