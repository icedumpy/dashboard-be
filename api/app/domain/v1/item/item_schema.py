from pydantic import BaseModel, Field
from typing import List, Optional

class FixRequestBody(BaseModel):
    image_ids: List[int] = Field(..., example=[101, 102, 103])
    note: Optional[str] = Field(None, example="Fixed defect using patching method")

    class Config:
        json_schema_extra = {
            "example": {
                "image_ids": [101, 102, 103],
                "note": "Fixed defect using patching method"
            }
        }
        
class DecisionRequestBody(BaseModel):
    decision: str = Field(..., example="APPROVE")  # APPROVE or REJECT
    reject_reason: Optional[str] = Field(None, example="QC failed at visual inspection")

    class Config:
        json_schema_extra = {
            "example": {
                "decision": "REJECT",
                "reject_reason": "QC failed at visual inspection"
            }
        }
