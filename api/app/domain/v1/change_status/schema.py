from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict

class StatusChangeRequestCreate(BaseModel):
    item_id: int
    to_status_id: int
    reason: Optional[str] = None
    meta: Optional[Dict] = None
    defect_type_ids: Optional[List[int]] = None

class StatusChangeRequestOut(BaseModel):
    id: int
    item_id: int
    from_status_id: int
    to_status_id: int
    state: str
    requested_by: int
    requested_at: str
    approved_by: Optional[int] = None
    approved_at: Optional[str] = None
    reason: Optional[str] = None
    meta: Optional[Dict] = None
    defect_type_ids: List[int] = []


class DecisionRequestBody(BaseModel):
    decision: str = Field(
        ...,
        description="Final decision",
        json_schema_extra={"example": "APPROVED"},
    )
    note: Optional[str] = Field(
        None,
        description="Optional note",
        json_schema_extra={"example": "QC failed at visual inspection"},
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "decision": "APPROVED",
                "note": "Approved",
            }
        }
    )



class SummaryOut(BaseModel):
    roll: int
    bundle: int
    total: int

class PaginationOut(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int

class ListResponseOut(BaseModel):
    data: List[StatusChangeRequestOut]
    summary: SummaryOut
    pagination: PaginationOut