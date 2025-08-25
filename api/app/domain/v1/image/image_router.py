import shutil
import os, mimetypes

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import FileResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from pathlib import Path

from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import User
from app.core.db.repo.models import Item, ItemImage
from app.utils.helper.helper import require_role, safe_fs_path

router = APIRouter()

@router.post("/upload")
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
        dest = base / f"{im.id}{ext}"  
        with dest.open("wb") as w:
            shutil.copyfileobj(f.file, w)
        im.path = str(dest)
        out.append({"id": im.id, "path": im.path, "kind": im.kind})

    await db.commit()
    return {"data": out}


@router.get("/image/{image_path:path}")
async def get_image(
    image_path: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Stream a local image by DB path, with authorization.
    Example path: 2025-08/21/line_3/roll/250814002D05/capture/698877.jpg
    """
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])

    # Authorize via DB ownership
    row = await db.execute(
        select(ItemImage, Item.line_id)
        .join(Item, Item.id == ItemImage.item_id)
        .where(ItemImage.path == image_path)
    )
    found = row.first()
    if not found:
        raise HTTPException(status_code=404, detail="Image not found")

    img: ItemImage = found[0]
    owner_line_id: int = found[1]

    # Optional soft-delete guard
    if getattr(img, "deleted_at", None):
        raise HTTPException(status_code=404, detail="Image not found")

    # Same-line rule (INSPECTOR can view all)
    if user.role != "INSPECTOR" and user.line_id != owner_line_id:
        raise HTTPException(status_code=403, detail="Cross-line view not allowed")

    # Resolve and serve file
    fs_path = safe_fs_path(image_path)
    if not fs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")

    media_type = mimetypes.guess_type(fs_path.name)[0] or "application/octet-stream"
    headers = {"Cache-Control": "public, max-age=86400, immutable"}
    return FileResponse(str(fs_path), media_type=media_type, headers=headers)