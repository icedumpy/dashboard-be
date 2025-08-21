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
        ('op_3a','Operator L3A', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sA), TRUE),
        ('op_3b','Operator L3B', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sB), TRUE),
        ('op_3c','Operator L3C', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sC), TRUE),
        ('op_3d','Operator L3D', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l3),(SELECT id FROM sD), TRUE),
        ('op_4a','Operator L4A', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sA), TRUE),
        ('op_4b','Operator L4B', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sB), TRUE),
        ('op_4c','Operator L4C', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sC), TRUE),
        ('op_4d','Operator L4D', crypt('changeme', gen_salt('bf', 12)),'OPERATOR',(SELECT id FROM l4),(SELECT id FROM sD), TRUE),
        ('qc_3a','QC L3A', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sA), TRUE),
        ('qc_3b','QC L3B', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sB), TRUE),
        ('qc_3c','QC L3C', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sC), TRUE),
        ('qc_3d','QC L3D', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l3),(SELECT id FROM sD), TRUE),
        ('qc_4a','QC L4A', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sA), TRUE),
        ('qc_4b','QC L4B', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sB), TRUE),
        ('qc_4c','QC L4C', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sC), TRUE),
        ('qc_4d','QC L4D', crypt('changeme', gen_salt('bf', 12)),'INSPECTOR',(SELECT id FROM l4),(SELECT id FROM sD), TRUE),
        ('viewer','Viewer', crypt('changeme', gen_salt('bf', 12)),'VIEWER',NULL,NULL, TRUE)
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


      -- ROLL: DEFECT (BOTTOM)
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3'),
          ins AS (
            INSERT INTO qc.items
              (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
              item_status_id,ai_note)
            SELECT
              'ROLL', line.id, '13W10C2MB','462263','3D3G256046',193, now()-interval '30 min',
              (SELECT id FROM qc.item_statuses WHERE code='DEFECT'),
              'Defect: ด้านล่าง'
            FROM line RETURNING id
          )
      INSERT INTO qc.item_defects(item_id, defect_type_id, meta)
      SELECT ins.id,(SELECT id FROM qc.defect_types WHERE code='BOTTOM'),
              '{"source":"AI"}'::jsonb
      FROM ins;

      -- ROLL: DEFECT (TOP)
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3'),
            ins AS (
              INSERT INTO qc.items
                (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
                item_status_id,ai_note)
              SELECT
                'ROLL', line.id, '13W10C2MB','462264','3D3G256047',190, now()-interval '1 hour',
                (SELECT id FROM qc.item_statuses WHERE code='DEFECT'),
                'Defect: ด้านบน'
              FROM line RETURNING id
            )
      INSERT INTO qc.item_defects(item_id, defect_type_id, meta)
      SELECT ins.id,(SELECT id FROM qc.defect_types WHERE code='TOP'),
              '{"source":"AI"}'::jsonb
      FROM ins;

      -- ROLL: DEFECT (BARCODE)
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3'),
            ins AS (
              INSERT INTO qc.items
                (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
                item_status_id,ai_note)
              SELECT
                'ROLL', line.id, '13W10C2MB','462265','3D3G256048',185, now()-interval '2 hour',
                (SELECT id FROM qc.item_statuses WHERE code='DEFECT'),
                'Defect: บาร์โค้ด'
              FROM line RETURNING id
            )
      INSERT INTO qc.item_defects(item_id, defect_type_id, meta)
      SELECT ins.id,(SELECT id FROM qc.defect_types WHERE code='BARCODE'),
              '{"source":"AI"}'::jsonb
      FROM ins;

      -- ROLL: SCRAP (AI ตรงๆ) + operator confirm
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3'),
      ins AS (
        INSERT INTO qc.items
          (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
            item_status_id,ai_note,scrap_confirmed_by,scrap_confirmed_at)
        SELECT 'ROLL', line.id, '13W10C2MB','451070','3D3G256045',270, now()-interval '3 hour',
                (SELECT id FROM qc.item_statuses WHERE code='SCRAP'),
                'Scrap (ไม่พบฉลาก)',
                (SELECT id FROM "user".users WHERE username='op_3a'), now()
        FROM line RETURNING id
      )
      INSERT INTO qc.item_events(item_id,actor_id,event_type,to_status_id,details)
      SELECT id,(SELECT id FROM "user".users WHERE username='op_3a'),
            'OPERATOR_CONFIRM_SCRAP',(SELECT id FROM qc.item_statuses WHERE code='SCRAP'),
            '{"note":"AI scrap confirmed"}'::jsonb
      FROM ins;

      -- ROLL: SCRAP (ยังไม่ confirm)
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3')
      INSERT INTO qc.items
        (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
          item_status_id,ai_note)
      SELECT 'ROLL', line.id, '13W10C2MB','451071','3D3G256049',275, now()-interval '4 hour',
              (SELECT id FROM qc.item_statuses WHERE code='SCRAP'),
              'Scrap (AI detected)' FROM line;

      -- BUNDLE: DEFECT (LABEL)
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='4'),
            ins AS (
              INSERT INTO qc.items
                (station,line_id,product_code,bundle_number,job_order_number,roll_width,detected_at,
                item_status_id,ai_note)
              SELECT
                'BUNDLE', line.id, '22X90Y7AA','461182','4A9B128099',250, now()-interval '2 hour',
                (SELECT id FROM qc.item_statuses WHERE code='DEFECT'),
                'Defect: ฉลาก'
              FROM line
              RETURNING id
            )
      INSERT INTO qc.item_defects(item_id, defect_type_id, meta)
      SELECT ins.id,(SELECT id FROM qc.defect_types WHERE code='LABEL'),
              '{"note":"missing label"}'::jsonb
      FROM ins;

      -- BUNDLE: DEFECT (BARCODE)
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='4'),
            ins AS (
              INSERT INTO qc.items
                (station,line_id,product_code,bundle_number,job_order_number,roll_width,detected_at,
                item_status_id,ai_note)
              SELECT
                'BUNDLE', line.id, '22X90Y7AA','461183','4A9B128100',255, now()-interval '3 hour',
                (SELECT id FROM qc.item_statuses WHERE code='DEFECT'),
                'Defect: บาร์โค้ด'
              FROM line
              RETURNING id
            )
      INSERT INTO qc.item_defects(item_id, defect_type_id, meta)
      SELECT ins.id,(SELECT id FROM qc.defect_types WHERE code='BARCODE'),
              '{"note":"barcode unreadable"}'::jsonb
      FROM ins;

      -- ROLL: NORMAL
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3')
      INSERT INTO qc.items
        (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
          item_status_id,ai_note)
      SELECT 'ROLL', line.id, '13W10C2MB','462266','3D3G256050',200, now()-interval '5 hour',
              (SELECT id FROM qc.item_statuses WHERE code='NORMAL'),
              'OK' FROM line;

      -- ROLL: QC_PASSED
      WITH line AS (SELECT id FROM qc.production_lines WHERE code='3')
      INSERT INTO qc.items
        (station,line_id,product_code,roll_number,job_order_number,roll_width,detected_at,
          item_status_id,ai_note)
      SELECT 'ROLL', line.id, '13W10C2MB','462267','3D3G256051',210, now()-interval '6 hour',
              (SELECT id FROM qc.item_statuses WHERE code='QC_PASSED'),
              'QC passed after recheck' FROM line;
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
