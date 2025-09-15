from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import time


class ProductionLineOut(BaseModel):
    id: int
    code: Optional[str] = None
    name: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class ShiftOut(BaseModel):
    start_time: Optional[time] = None
    end_time: Optional[time] = None

class UserOut(BaseModel):
    id: int
    username: Optional[str] = None
    display_name: str
    role: str
    is_active: bool
    line: Optional[ProductionLineOut] = None
    shift: Optional[ShiftOut] = None
    model_config = ConfigDict(from_attributes=True)

class LoginIn(BaseModel):
    username: str
    password: str

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshIn(BaseModel):
    refresh_token: str
