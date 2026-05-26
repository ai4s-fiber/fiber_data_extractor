"""Database initialization script — creates all tables and a default admin user."""

import asyncio
from sqlalchemy import text
from app.core.database import engine
from app.core.security import hash_password
from app.models.base import Base

# Import all models so they register with Base.metadata
from app.models.user import User
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.paper import Paper
from app.models.extraction_job import ExtractionJob
from app.models.page_inventory import PageInventory
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.review_log import ReviewLog
from app.models.export_job import ExportJob


async def init_db():
    async with engine.begin() as conn:
        print("Dropping existing tables for clean schema migration...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Creating all tables...")
        await conn.run_sync(Base.metadata.create_all)
    print("All tables created.")

    # Create default admin and seed data
    from app.core.database import async_session_factory
    async with async_session_factory() as session:
        from sqlalchemy import select
        
        # 1. Create Default Admin
        result = await session.execute(
            select(User).where(User.email == "admin@fiber.local")
        )
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = User(
                email="admin@fiber.local",
                name="默认管理员",
                password_hash=hash_password("admin123"),
                is_superadmin=True,
            )
            session.add(admin)
            await session.flush()
            print("Default admin created: admin@fiber.local / admin123")

        # 2. Create Default Student and Reviewer
        student_res = await session.execute(select(User).where(User.email == "student@fiber.local"))
        student = student_res.scalar_one_or_none()
        if student is None:
            student = User(
                email="student@fiber.local",
                name="学生小明",
                password_hash=hash_password("student123"),
                is_superadmin=False,
            )
            session.add(student)
            await session.flush()

        reviewer_res = await session.execute(select(User).where(User.email == "reviewer@fiber.local"))
        reviewer = reviewer_res.scalar_one_or_none()
        if reviewer is None:
            reviewer = User(
                email="reviewer@fiber.local",
                name="张教授(审核员)",
                password_hash=hash_password("reviewer123"),
                is_superadmin=False,
            )
            session.add(reviewer)
            await session.flush()

        await session.commit()


if __name__ == "__main__":
    asyncio.run(init_db())

