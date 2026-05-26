"""User management routes (superadmin only)."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_superadmin, get_current_user
from app.core.security import hash_password
from app.models.user import User
from app.schemas.auth import UserOut, UserAdminCreate, UserAdminUpdate

router = APIRouter(prefix="/users", tags=["用户管理"])


class UserLookupItem(BaseModel):
    id: int
    email: str
    name: str


@router.get("/lookup", response_model=list[UserLookupItem])
async def lookup_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取用户列表用于成员选择下拉框（所有已登录用户可用）。"""
    result = await db.execute(
        select(User.id, User.name, User.email)
        .where(User.is_active == True)
        .order_by(User.name)
    )
    rows = result.all()
    return [{"id": r[0], "name": r[1], "email": r[2]} for r in rows]


@router.get("", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin),
):
    """列出所有用户（仅超级管理员）。"""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserAdminCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin),
):
    """创建新用户（仅超级管理员）。"""
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该邮箱已注册")
    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        is_superadmin=body.is_superadmin,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserAdminUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin),
):
    """修改用户信息（仅超级管理员）。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.name is not None:
        user.name = body.name
    if body.email is not None:
        existing = await db.execute(select(User).where(User.email == body.email, User.id != user_id))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="该邮箱已被其他用户使用")
        user.email = body.email
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superadmin is not None:
        user.is_superadmin = body.is_superadmin
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin),
):
    """删除用户（仅超级管理员）。"""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    await db.delete(user)
