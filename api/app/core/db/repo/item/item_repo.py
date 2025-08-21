from sqlalchemy import text
from app.core.db.session import engine


def fetch_counters(limit: int = 100):
    with engine.connect() as conn:
        rs = conn.execute(text(
            """
            SELECT line_id, station, hour_bucket, passed_count, defect_count, scrap_count, pending_qc_count
            FROM qc.mv_qc_counter
            ORDER BY hour_bucket DESC
            LIMIT :limit
            """
        ), {"limit": limit})
        cols = rs.keys()
        return [dict(zip(cols, row)) for row in rs]