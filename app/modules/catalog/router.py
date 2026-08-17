from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Category

router = APIRouter(prefix=settings.API_V1_PREFIX + "/catalog", tags=["catalog"])


@router.get("/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Category).where(Category.is_active == True).order_by(Category.name)
    )
    categories = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "slug": c.slug,
            "parent_id": str(c.parent_id) if c.parent_id else None,
        }
        for c in categories
    ]