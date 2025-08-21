from pydantic import BaseModel, Field
from typing import List, Optional

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