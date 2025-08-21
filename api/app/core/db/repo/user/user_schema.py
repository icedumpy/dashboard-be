from pydantic import BaseModel

class UserOut(BaseModel):
    id: int
    username: str | None
    display_name: str
    role: str
    is_active: bool
    class Config:
        from_attributes = True

class LoginIn(BaseModel):
    username: str
    password: str

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshIn(BaseModel):
    refresh_token: str
