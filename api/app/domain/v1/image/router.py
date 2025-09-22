import shutil
import mimetypes

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import FileResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import User
from app.core.db.repo.models import Item, ItemImage
from app.utils.helper.helper import require_role, safe_fs_path, get_base_image_relpath

router = APIRouter()

@router.post("/upload")
async def upload_images(
    files: List[UploadFile] = File(...),
    item_id: Optional[int] = Form(None),
    kind: Optional[str] = Form("FIX"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Max 10 files")
    out = []
    
    current_base_path = await get_base_image_relpath(db=db,item_id=item_id,kind=kind)
    
    base = Path("./images") / current_base_path
    base.mkdir(parents=True, exist_ok=True)
    
    imgs = []
    for _ in files:
        im = ItemImage(item_id=item_id, review_id=None, kind=kind, path="", uploaded_by=user.id)
        db.add(im)
        imgs.append(im)
    await db.flush() 

    for f, im in zip(files, imgs):
        ext = (Path(f.filename).suffix or ".jpg").lower()
        dest = Path(base) / f"{im.id}{ext}"  
        with dest.open("wb") as w:
            shutil.copyfileobj(f.file, w)
        im.path = current_base_path  + f"/{im.id}{ext}"  
        out.append({"id": im.id, "path": im.path, "kind": im.kind})

    await db.commit()
    return {"data": out}


@router.get("/{image_path:path}")
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

    row = await db.execute(
        select(ItemImage, Item.line_id)
        .join(Item, Item.id == ItemImage.item_id)
        .where(ItemImage.path == image_path)
    )
    found = row.first()
    if not found:
        raise HTTPException(status_code=404, detail="Image not found")

    img: ItemImage = found[0]

    if getattr(img, "deleted_at", None):
        raise HTTPException(status_code=404, detail="Image not found")

    fs_path = safe_fs_path(image_path)
    if not fs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")

    media_type = mimetypes.guess_type(fs_path.name)[0] or "application/octet-stream"
    headers = {"Cache-Control": "public, max-age=86400, immutable"}
    return FileResponse(str(fs_path), media_type=media_type, headers=headers)