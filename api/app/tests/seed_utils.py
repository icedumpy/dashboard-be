# tests/seed_utils.py
from sqlalchemy import insert
from sqlalchemy.orm import Session

# EXAMPLE imports — change to your actual models
from app.core.db.repo.models import User, ItemStatus, Item

def seed_users(db: Session, n: int = 1) -> list[int]:
    rows = [
        {
            "email": f"tester{i}@example.com",
            "name": f"Tester {i}",
            "password_hash": "fakehash",  # or whatever your model uses
        }
        for i in range(n)
    ]
    db.execute(insert(User).values(rows))
    db.flush()

    # Return created IDs (Postgres will fill them if serial/identity)
    ids = [u.id for u in db.query(User).order_by(User.id.desc()).limit(n).all()][::-1]
    return ids

def seed_item_statuses(db: Session) -> dict[str, int]:
    """
    Return a mapping like {"DEFECT": 1, "QC_PASSED": 2, ...}
    Only inserts if table is empty.
    """
    if db.query(ItemStatus).count() == 0:
        db.execute(
            insert(ItemStatus).values(
                [
                    {"code": "DEFECT", "name_th": "มีตำหนิ", "display_order": 1},
                    {"code": "QC_PASSED", "name_th": "ผ่าน QC", "display_order": 999},
                ]
            )
        )
        db.flush()

    rows = db.query(ItemStatus).all()
    return {r.code: r.id for r in rows}

def seed_items(db: Session, *, count: int = 1, status_id: int | None = None) -> list[int]:
    payload = []
    for i in range(count):
        payload.append(
            {
                "product_code": f"PROD-{i}",
                "line_id": 3,
                "roll_number": f"R{i:04d}",
                "bundle_number": None,
                "job_order_number": "JOB-001",
                "roll_width": 1_250.0,
                "item_status_id": status_id,
            }
        )
    db.execute(insert(Item).values(payload))
    db.flush()
    ids = [r.id for r in db.query(Item).order_by(Item.id.desc()).limit(count).all()][::-1]
    return ids
