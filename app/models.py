from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from sqlalchemy import Integer, String, Text, Float, ForeignKey, DateTime
from datetime import datetime

Base = declarative_base()


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comma-separated list of keywords for simple rule-based matching
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)

    audio_items: Mapped[list["AudioItem"]] = relationship(
        "AudioItem", back_populates="category"
    )


class AudioItem(Base):
    __tablename__ = "audio_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(512))
    transcript: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    category: Mapped[Category | None] = relationship(
        "Category", back_populates="audio_items"
    )

