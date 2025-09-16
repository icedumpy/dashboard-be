from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any, Dict
from datetime import datetime
from app.core.db.repo.models import EStation, EItemStatusCode

OperatorStatus = Literal["DEFECT", "SCRAP", "NORMAL"]

class ActorOut(BaseModel):
    id: int
    username: Optional[str] = None
    display_name: Optional[str] = None

class ItemEventOut(BaseModel):
    id: int
    event_type: str
    actor: ActorOut
    from_status_id: Optional[int] = None
    from_status_code: Optional[str] = None
    to_status_id: Optional[int] = None
    to_status_code: Optional[str] = None
    defects: List[str]
    created_at: str

class UpdateItemStatusBody(BaseModel):
    status: OperatorStatus = Field(..., description="DEFECT | SCRAP | NORMAL")
    defect_type_ids: Optional[List[int]] = Field(
        None,
        description="Required when changing NORMAL -> DEFECT. Replaces existing defects if provided."
    )
    meta: Optional[Dict[str, Any]] = None 

class FixRequestBody(BaseModel):
    image_ids: List[int] = Field(..., example=[1])
    note: Optional[str] = Field(None, example="Fixed defect using patching method")

    class Config:
        json_schema_extra = {
            "example": {
                "image_ids": [1],
                "note": "Fixed defect using patching method"
            }
        }
        
class ItemReportRequest(BaseModel):
    line_id: int = Field(..., ge=1, description="Numeric line id")
    station: EStation = Field(..., description="ROLL or BUNDLE")

    # optional filters (same as list UI)
    product_code: Optional[str] = Field(None, description="contains match")
    number: Optional[str] = Field(None, description="roll_number or bundle_number (contains)")
    job_order_number: Optional[str] = Field(None, description="contains match")
    roll_width_min: Optional[float] = Field(None, ge=0)
    roll_width_max: Optional[float] = Field(None, ge=0)
    status: Optional[List[EItemStatusCode]] = Field(None, description="repeatable status codes")
    detected_from: Optional[datetime] = Field(None, description="ISO8601")
    detected_to: Optional[datetime] = Field(None, description="ISO8601")

    model_config = {
        "json_schema_extra": {
            "example": {
                "line_id": 1,
                "station": "ROLL",
                "product_code": "13W10",
                "number": "4622",
                "job_order_number": "3D3G",
                "roll_width_min": 100,
                "roll_width_max": 330,
                "status": ["DEFECT", "SCRAP", "QC_PASSED"],
                "detected_from": "2025-08-20T00:00:00Z",
                "detected_to": "2025-08-22T23:59:59Z",
            }
        }
    }
