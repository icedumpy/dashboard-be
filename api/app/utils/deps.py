from fastapi import Query

def LimitQuery(default: int = 100, max_value: int = 500):
    def _limit(limit: int = Query(default, ge=1, le=max_value)):
        return limit
    return _limit