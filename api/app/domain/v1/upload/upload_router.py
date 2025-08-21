# app/domain/v1/uploads_router.py
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from pathlib import Path
import shutil
from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.user.user_entity import User
from app.core.db.repo.models import ItemImage

router = APIRouter()

@router.post("")
async def upload_images(
    files: List[UploadFile] = File(...),
    item_id: Optional[int] = Form(None),
    kind: Optional[str] = Form("FIX"),  # DETECTED|FIX|OTHER
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Max 10 files")
    out = []
    today = datetime.utcnow()
    base = Path("./images") / f"{today:%Y-%m}" / f"{today:%d}"
    base.mkdir(parents=True, exist_ok=True)

    # Insert rows first to get ids (for row_number in filename)
    imgs = []
    for _ in files:
        im = ItemImage(item_id=item_id, review_id=None, kind=kind, path="", uploaded_by=user.id)
        db.add(im)
        imgs.append(im)
    await db.flush()  # get ids

    # Save files with row_number (id)
    for f, im in zip(files, imgs):
        ext = (Path(f.filename).suffix or ".jpg").lower()
        dest = base / f"{im.id}{ext}"   # ./images/{yyyy}-{mm}/{dd}/{row_number}.{ext}
        with dest.open("wb") as w:
            shutil.copyfileobj(f.file, w)
        im.path = str(dest)
        out.append({"id": im.id, "path": im.path, "kind": im.kind})

    await db.commit()
    return {"data": out}
