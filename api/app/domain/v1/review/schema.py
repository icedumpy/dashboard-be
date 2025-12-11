from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class DecisionRequestBody(BaseModel):
    decision: Literal["APPROVED", "REJECTED"] = Field(
        ...,
        description="Final QC decision",
        json_schema_extra={"example": "APPROVED"},
    )
    note: str | None = Field(
        None,
        description="Optional reason/remark",
        json_schema_extra={"example": "QC failed at visual inspection"},
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "decision": "REJECTED",
                "note": "QC failed at visual inspection",
            }
        }
    )
