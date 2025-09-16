from pydantic import BaseModel, Field
from typing import Optional

class DecisionRequestBody(BaseModel):
    decision: str = Field(..., example="APPROVED")  # APPROVE or REJECT
    note: Optional[str] = Field(None, example="QC failed at visual inspection")

    class Config:
        json_schema_extra = {
            "example": {
                "decision": "REJECTED",
                "note": "QC failed at visual inspection"
            }
        }
