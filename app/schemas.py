from pydantic import BaseModel
from typing import Optional, Dict
from datetime import datetime


class CategoryBase(BaseModel):
    name: str
    description: Optional[str] = None
    # Comma-separated keywords string, e.g. "bank, loan, credit"
    keywords: Optional[str] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None


class CategoryOut(CategoryBase):
    id: int

    class Config:
        from_attributes = True


class AudioItemBase(BaseModel):
    category_id: Optional[int] = None
    confidence: Optional[float] = None


class AudioItemUpdate(AudioItemBase):
    pass


class AudioItemOut(AudioItemBase):
    id: int
    original_filename: str
    stored_path: str
    transcript: str
    created_at: datetime

    class Config:
        from_attributes = True


class SummaryOut(BaseModel):
    total: int
    by_category: Dict[str, Dict[str, float | int]]

