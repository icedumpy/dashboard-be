# api/app/domain/v1/camera/schema.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CameraBase(BaseModel):
    channel_id: int = Field(..., description="Logical channel, e.g. 3, 4")
    line_id: Optional[int] = Field(
        None,
        description="FK to qc.production_lines.id (nullable)",
    )
    camera_name: str = Field(..., description="Human readable camera name")
    # camera_ip: str = Field(..., description="Camera IP or hostname")




class CameraOut(CameraBase):
    id: int
    created_at: datetime
    updated_at: datetime

class CameraStreamUrlOut(BaseModel):
    url: str
    
class CameraResetFocusOut(BaseModel):
    status: bool
