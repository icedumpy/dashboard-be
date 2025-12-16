# api/app/domain/v1/camera/router.py
from typing import List

from fastapi import APIRouter, Depends

from .schema import (
    CameraOut,
    CameraStreamUrlOut,
    CameraResetFocusOut,
)
from .service import CameraService, get_service

router = APIRouter()
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
    return await service.get_stream_url(camera_id)


@router.post(
  '/reset-focus',
  response_model=CameraResetFocusOut,
  summary='Reset camera focus',
)
async def reset_camera(
    service: CameraService = Depends(get_service),
):
  return service.reset_focus()
