# api/app/domain/v1/camera/router.py
import threading
import time
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends

from .schema import (
    CameraOut,
    CameraStreamUrlOut,
    CameraResetFocusOut,
)
from .service import CameraService, get_service

router = APIRouter()
_STREAM_URL_CACHE_TTL_SECONDS = 5.0
_stream_url_cache: Dict[int, Tuple[float, CameraStreamUrlOut]] = {}
_stream_url_cache_lock = threading.Lock()


@router.get(
    "",
    response_model=List[CameraOut],
    summary="List all cameras",
)
async def list_cameras(line_id: int, service: CameraService = Depends(get_service)):
    return await service.list_cameras(line_id)


@router.get(
    "/{camera_id}",
    response_model=CameraOut,
    summary="Get camera by ID",
)
async def get_camera(
    camera_id: int,
    service: CameraService = Depends(get_service),
):
    return await service.get_camera_by_id(camera_id)

@router.get(
    "/{camera_id}/stream-url",
    response_model=CameraStreamUrlOut,
    summary="Get HLS stream URL for a channel",
)
async def get_stream_url(
    camera_id: int,
    service: CameraService = Depends(get_service),
):
    now = time.monotonic()
    with _stream_url_cache_lock:
        cached = _stream_url_cache.get(camera_id)
        if cached and (now - cached[0]) < _STREAM_URL_CACHE_TTL_SECONDS:
            return cached[1]

    stream_url = await service.get_stream_url(camera_id)

    with _stream_url_cache_lock:
        _stream_url_cache[camera_id] = (now, stream_url)

    return stream_url


@router.post(
  '/reset-focus',
  response_model=CameraResetFocusOut,
  summary='Reset camera focus',
)
async def reset_camera(
    service: CameraService = Depends(get_service),
):
  return service.reset_focus()
