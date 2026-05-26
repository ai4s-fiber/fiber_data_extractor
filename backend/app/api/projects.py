"""Project routes: CRUD, member management."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_project_role
from app.models.user import User
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.paper import Paper
from app.models.candidate_record import CandidateRecord
from app.schemas.project import (
    ProjectCreate, ProjectOut, ProjectUpdate,
    MemberAdd, MemberOut, MemberRoleUpdate,
)

router = APIRouter(prefix="/projects", tags=["项目"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可访问的所有项目。"""
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id, Project.archived_at.is_(None))
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()
    out = []
    for p in projects:
        # Get stats
        paper_count_r = await db.execute(
            select(func.count(Paper.id)).where(Paper.project_id == p.id)
        )
        pending_r = await db.execute(
            select(func.count(CandidateRecord.id)).where(
                CandidateRecord.project_id == p.id,
                CandidateRecord.review_status == "pending",
            )
        )
        approved_r = await db.execute(
            select(func.count(CandidateRecord.id)).where(
                CandidateRecord.project_id == p.id,
                CandidateRecord.review_status == "approved",
            )
        )
        po = ProjectOut.model_validate(p)
        po.paper_count = paper_count_r.scalar() or 0
        po.pending_count = pending_r.scalar() or 0
        po.approved_count = approved_r.scalar() or 0
        out.append(po)
    return out


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建项目，创建者自动成为管理员。"""
    project = Project(
        name=body.name,
        description=body.description,
        created_by=user.id,
    )
    db.add(project)
    await db.flush()
    # Creator becomes admin
    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role="admin",
    )
    db.add(member)
    await db.flush()
    await db.refresh(project)
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取项目详情。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新项目信息（仅管理员）。"""
    await require_project_role(project_id, user, db, ["admin"])
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await db.flush()
    await db.refresh(project)
    return ProjectOut.model_validate(project)


# --- Members ---


@router.get("/{project_id}/members", response_model=list[MemberOut])
async def list_members(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出项目成员。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(
        select(ProjectMember, User.name, User.email)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
    )
    rows = result.all()
    out = []
    for member, uname, uemail in rows:
        mo = MemberOut.model_validate(member)
        mo.user_name = uname
        mo.user_email = uemail
        out.append(mo)
    return out


@router.post("/{project_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: int,
    body: MemberAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """添加项目成员（仅管理员）。"""
    await require_project_role(project_id, user, db, ["admin"])
    # Check user exists
    target = await db.execute(select(User).where(User.id == body.user_id))
    if target.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # Check not already member
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == body.user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="用户已是项目成员")
    member = ProjectMember(
        project_id=project_id,
        user_id=body.user_id,
        role=body.role,
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return MemberOut.model_validate(member)


@router.patch("/{project_id}/members/{member_id}", response_model=MemberOut)
async def update_member_role(
    project_id: int,
    member_id: int,
    body: MemberRoleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改成员角色（仅管理员）。"""
    await require_project_role(project_id, user, db, ["admin"])
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if body.role not in ("admin", "reviewer", "student"):
        raise HTTPException(status_code=400, detail="无效的角色")
    member.role = body.role
    await db.flush()
    await db.refresh(member)
    return MemberOut.model_validate(member)


@router.delete("/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: int,
    member_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """移除项目成员（仅管理员）。"""
    await require_project_role(project_id, user, db, ["admin"])
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    await db.delete(member)


# --- LLM API Configuration Endpoints ---
from app.schemas.project import ProjectLLMConfigUpdate, ProjectLLMConfigOut
import httpx

@router.get("/{project_id}/llm-config", response_model=ProjectLLMConfigOut)
async def get_project_llm_config(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取大模型配置（密码脱敏处理）。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # Mask API key for security
    raw_key = project.llm_api_key or ""
    masked = ""
    if raw_key:
        if len(raw_key) > 8:
            masked = f"{raw_key[:6]}...{raw_key[-4:]}"
        else:
            masked = "******"
            
    return ProjectLLMConfigOut(
        llm_provider=project.llm_provider,
        llm_api_key_masked=masked,
        llm_base_url=project.llm_base_url,
        llm_model=project.llm_model,
    )


@router.put("/{project_id}/llm-config", response_model=ProjectLLMConfigOut)
async def update_project_llm_config(
    project_id: int,
    body: ProjectLLMConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新大模型配置（仅项目管理员或审核员）。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if body.llm_provider is not None:
        project.llm_provider = body.llm_provider
    if body.llm_api_key is not None:
        # If user submits masked placeholder, do not overwrite with masked key
        if not ("..." in body.llm_api_key or body.llm_api_key == "******"):
            project.llm_api_key = body.llm_api_key
    if body.llm_base_url is not None:
        project.llm_base_url = body.llm_base_url
    if body.llm_model is not None:
        project.llm_model = body.llm_model
        
    await db.flush()
    await db.refresh(project)
    
    raw_key = project.llm_api_key or ""
    masked = ""
    if raw_key:
        if len(raw_key) > 8:
            masked = f"{raw_key[:6]}...{raw_key[-4:]}"
        else:
            masked = "******"
            
    return ProjectLLMConfigOut(
        llm_provider=project.llm_provider,
        llm_api_key_masked=masked,
        llm_base_url=project.llm_base_url,
        llm_model=project.llm_model,
    )


@router.post("/{project_id}/llm-config/test")
async def test_llm_connection(
    project_id: int,
    body: ProjectLLMConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """测试指定配置下大模型 API 的连通性。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    
    # Use submitted key, or fetch existing from DB if placeholder submitted
    api_key = body.llm_api_key
    if not api_key or "..." in api_key or api_key == "******":
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if project:
            api_key = project.llm_api_key
            
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")
        
    base_url = body.llm_base_url or "https://api.openai.com/v1"
    model = body.llm_model or "gpt-4o"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Hello! Reply with 'OK' only to confirm API accessibility."}
        ],
        "max_tokens": 10,
    }
    
    url = f"{base_url.rstrip('/')}/chat/completions"
    
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                resp_json = response.json()
                reply = resp_json["choices"][0]["message"]["content"].strip()
                return {"success": True, "message": f"连接成功！模型响应: {reply}"}
            else:
                return {
                    "success": False,
                    "message": f"大模型服务返回 HTTP 错误 {response.status_code}: {response.text[:300]}"
                }
    except Exception as e:
        return {"success": False, "message": f"请求大模型接口失败: {str(e)}"}

