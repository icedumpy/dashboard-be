from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any, Dict

class StatusChangeRequestCreate(BaseModel):
    item_id: int
    # from_status_id: int
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

    # class Config:
    #     orm_mode = True


class DecisionRequestBody(BaseModel):
    decision: str = Field(..., example="APPROVED")  # APPROVE or REJECT
    note: Optional[str] = Field(None, example="QC failed at visual inspection")

    class Config:
        json_schema_extra = {
            "example": {
                "decision": "APPROVED",
                "note": "Approved"
            }
        }

