from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi import Request
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
import os
import uuid
import re
from typing import List, Optional, Dict

from .models import Base, Category, AudioItem
from .schemas import (
    CategoryCreate,
    CategoryUpdate,
    CategoryOut,
    AudioItemOut,
    AudioItemUpdate,
    SummaryOut,
)
from .services import transcribe_uzbek_audio, categorize_text


DATABASE_URL = "sqlite+aiosqlite:///./audio_ai.db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


app = FastAPI(title="Uzbek Audio AI Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _normalize_for_keywords(text: str) -> str:
    """
    Нормализация узбекского текста/кейвордов:
    - lower,
    - унификация/удаление апострофов,
    - удаление пунктуации,
    - схлопывание пробелов.
    """
    if not text:
        return ""
    text = text.lower()
    text = (
        text.replace("’", "'")
        .replace("`", "'")
        .replace("´", "'")
    )
    text = text.replace("'", "")
    text = re.sub(r"[^0-9a-zа-яёғқҳў ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@app.on_event("startup")
async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migration for SQLite when we add columns without Alembic
        result = await conn.exec_driver_sql("PRAGMA table_info(categories);")
        cols = {row[1] for row in result.fetchall()}
        if "keywords" not in cols:
            await conn.exec_driver_sql("ALTER TABLE categories ADD COLUMN keywords TEXT;")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "openai_api_key_set": bool(os.getenv("OPENAI_API_KEY")),
        "transcription_provider": os.getenv("TRANSCRIPTION_PROVIDER", "local_whisper"),
        "whisper_model_size": (
            os.getenv("WHISPER_MODEL_SIZE")
            or os.getenv("WHISPER_MODEL")
            or "medium"
        ),
    }


@app.post("/api/categories", response_model=CategoryOut)
async def create_category(
    payload: CategoryCreate, db: AsyncSession = Depends(get_db)
):
    category = Category(
        name=payload.name,
        description=payload.description,
        keywords=payload.keywords,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@app.get("/api/categories", response_model=List[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category))
    return result.scalars().all()


@app.get("/api/v1/categories", response_model=List[CategoryOut])
async def list_categories_v1(
    active_only: Optional[bool] = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Совместимый v1-роут, который возвращает список категорий.
    Параметр active_only сейчас игнорируется и оставлен для совместимости.
    """
    # На будущее можно фильтровать по активности, если появится соответствующее поле.
    return await list_categories(db=db)


@app.get("/api/categories/{category_id}", response_model=CategoryOut)
async def get_category(category_id: int, db: AsyncSession = Depends(get_db)):
    category = await db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@app.put("/api/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int, payload: CategoryUpdate, db: AsyncSession = Depends(get_db)
):
    category = await db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None:
        category.name = payload.name
    if payload.description is not None:
        category.description = payload.description
    if payload.keywords is not None:
        category.keywords = payload.keywords
    await db.commit()
    await db.refresh(category)
    return category


@app.delete("/api/categories/{category_id}")
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)):
    category = await db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    await db.delete(category)
    await db.commit()
    return {"ok": True}


@app.post("/api/audio", response_model=AudioItemOut)
async def upload_audio(
    file: UploadFile = File(...),
    override_category_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(await file.read())

    try:
        text = await transcribe_uzbek_audio(filepath)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # Surface the real cause to simplify local debugging
        raise HTTPException(status_code=500, detail=f"Failed to transcribe audio: {e}")

    result_category_id: Optional[int] = None
    confidence: Optional[float] = None

    # override_category_id:
    # - None => auto
    # - 0 => auto (convenient for some clients)
    # - >0 => force category
    if override_category_id is not None and int(override_category_id) > 0:
        forced = await db.get(Category, int(override_category_id))
        if not forced:
            raise HTTPException(status_code=400, detail="override_category_id not found")
        result_category_id = int(override_category_id)
        confidence = 1.0
    else:
        result = await db.execute(select(Category))
        categories = result.scalars().all()
        if categories:
            # 1) Keyword-based scoring (с нормализацией текста и кейвордов)
            normalized_text = _normalize_for_keywords(text or "")
            scores: Dict[int, float] = {}
            for c in categories:
                if c.keywords:
                    keywords = [
                        kw.strip()
                        for kw in c.keywords.split(",")
                        if kw.strip()
                    ]
                    score = 0.0
                    for kw in keywords:
                        kw_norm = _normalize_for_keywords(kw)
                        if not kw_norm:
                            continue
                        score += normalized_text.count(kw_norm)
                    if score > 0:
                        scores[c.id] = scores.get(c.id, 0.0) + score

            if scores:
                total_score = sum(scores.values())
                best_category_id = max(scores, key=scores.get)
                result_category_id = best_category_id
                confidence = scores[best_category_id] / total_score if total_score else 1.0
            else:
                # 2) Fallback to GPT-based semantic classification
                labels = [c.name for c in categories]
                probs = await categorize_text(text, labels)
                if probs:
                    best_label = max(probs, key=probs.get)
                    best_category = next(
                        (c for c in categories if c.name == best_label), None
                    )
                    if best_category:
                        result_category_id = best_category.id
                        confidence = probs[best_label]

    audio_item = AudioItem(
        original_filename=file.filename,
        stored_path=filename,
        transcript=text,
        category_id=result_category_id,
        confidence=confidence,
    )
    db.add(audio_item)
    await db.commit()
    await db.refresh(audio_item)
    return audio_item


@app.post("/api/v1/audio/upload", response_model=AudioItemOut)
async def upload_audio_v1(
    file: UploadFile = File(...),
    override_category_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Совместимый v1-роут, который проксирует в основной /api/audio.
    Удобно, если фронт/клиент уже заточен под /api/v1/audio/upload.
    """
    return await upload_audio(
        file=file,
        override_category_id=override_category_id,
        db=db,
    )


@app.get("/api/audio", response_model=List[AudioItemOut])
async def list_audio(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AudioItem))
    return result.scalars().all()


@app.get("/api/audio/{audio_id}", response_model=AudioItemOut)
async def get_audio(audio_id: int, db: AsyncSession = Depends(get_db)):
    audio = await db.get(AudioItem, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    return audio


@app.get("/api/audio/{audio_id}/download")
async def download_audio(audio_id: int, db: AsyncSession = Depends(get_db)):
    audio = await db.get(AudioItem, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    filepath = os.path.join(UPLOAD_DIR, audio.stored_path)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Stored audio file not found on disk")
    return FileResponse(
        filepath,
        filename=audio.original_filename,
        media_type="application/octet-stream",
    )


@app.post("/api/audio/{audio_id}/reprocess", response_model=AudioItemOut)
async def reprocess_audio(audio_id: int, db: AsyncSession = Depends(get_db)):
    audio = await db.get(AudioItem, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")

    filepath = os.path.join(UPLOAD_DIR, audio.stored_path)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Stored audio file not found on disk")

    try:
        text = await transcribe_uzbek_audio(filepath)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # Surface the real cause to simplify local debugging
        raise HTTPException(status_code=500, detail=f"Failed to transcribe audio: {e}")

    audio.transcript = text

    # Re-categorize using current categories/keywords
    result_category_id: Optional[int] = None
    confidence: Optional[float] = None

    result = await db.execute(select(Category))
    categories = result.scalars().all()
    if categories:
        normalized_text = _normalize_for_keywords(text or "")
        scores: Dict[int, float] = {}
        for c in categories:
            if c.keywords:
                keywords = [
                    kw.strip()
                    for kw in c.keywords.split(",")
                    if kw.strip()
                ]
                score = 0.0
                for kw in keywords:
                    kw_norm = _normalize_for_keywords(kw)
                    if not kw_norm:
                        continue
                    score += normalized_text.count(kw_norm)
                if score > 0:
                    scores[c.id] = scores.get(c.id, 0.0) + score

        if scores:
            total_score = sum(scores.values())
            best_category_id = max(scores, key=scores.get)
            result_category_id = best_category_id
            confidence = scores[best_category_id] / total_score if total_score else 1.0
        else:
            labels = [c.name for c in categories]
            probs = await categorize_text(text, labels)
            if probs:
                best_label = max(probs, key=probs.get)
                best_category = next((c for c in categories if c.name == best_label), None)
                if best_category:
                    result_category_id = best_category.id
                    confidence = probs[best_label]

    audio.category_id = result_category_id
    audio.confidence = confidence

    await db.commit()
    await db.refresh(audio)
    return audio


@app.put("/api/audio/{audio_id}", response_model=AudioItemOut)
async def update_audio(
    audio_id: int, payload: AudioItemUpdate, db: AsyncSession = Depends(get_db)
):
    audio = await db.get(AudioItem, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    if payload.category_id is not None:
        audio.category_id = payload.category_id
    if payload.confidence is not None:
        audio.confidence = payload.confidence
    await db.commit()
    await db.refresh(audio)
    return audio


@app.delete("/api/audio/{audio_id}")
async def delete_audio(audio_id: int, db: AsyncSession = Depends(get_db)):
    audio = await db.get(AudioItem, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    await db.delete(audio)
    await db.commit()
    return {"ok": True}


@app.get("/api/summary", response_model=SummaryOut)
async def summary(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AudioItem))
    items = result.scalars().all()

    total = len(items)
    by_category: Dict[str, Dict[str, float | int]] = {}

    cat_result = await db.execute(select(Category))
    categories = {c.id: c.name for c in cat_result.scalars().all()}

    for item in items:
        cat_name = categories.get(item.category_id, "Uncategorized")
        if cat_name not in by_category:
            by_category[cat_name] = {
                "count": 0,
                "avg_confidence": 0.0,
            }
        by_category[cat_name]["count"] += 1
        if item.confidence is not None:
            by_category[cat_name]["avg_confidence"] += float(item.confidence)

    for cat_name, data in by_category.items():
        if data["count"] > 0:
            data["avg_confidence"] = round(
                data["avg_confidence"] / data["count"], 3
            )

    return SummaryOut(total=total, by_category=by_category)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

