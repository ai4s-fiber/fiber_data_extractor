"""Pydantic schemas for authentication and users."""

from datetime import datetime
from pydantic import BaseModel, EmailStr


# --- Auth ---
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


# --- User ---
class UserCreate(BaseModel):
    email: str
    name: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool
    is_superadmin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class UserAdminCreate(BaseModel):
    email: str
    name: str
    password: str
    is_superadmin: bool = False


class UserAdminUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    is_active: bool | None = None
    is_superadmin: bool | None = None
