import os
import subprocess
import threading
from typing import Dict, List, Optional
from pathlib import Path
import requests
from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import time
import hashlib
from app.core.db.session import get_db
from .schema import CameraOut, CameraStreamUrlOut, CameraResetFocusOut
from app.core.config.config import settings
from urllib.parse import quote
import asyncio


_ffmpeg_processes: Dict[int, subprocess.Popen] = {}
_ffmpeg_started_at: Dict[int, float] = {}
_ffmpeg_lock = threading.Lock()
_PLAYLIST_FRESHNESS_SECONDS = 10.0
_STREAM_READY_TIMEOUT_SECONDS = 10.0
_STREAM_READY_POLL_SECONDS = 0.5


class CameraService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _fetch_camera_row_by_id(self, camera_id: int) -> Optional[dict]:
        stmt = text(
            """
            SELECT id,
              channel_id,
              line_id,
              camera_name,
              camera_ip,
              created_at,
              updated_at
            FROM qc.cameras
            WHERE id = :id
            """
        )
        result = await self.db.execute(stmt, {"id": camera_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_cameras(self, line_id: int) -> List[CameraOut]:
        stmt = text(
            """
            SELECT id,
              channel_id,
              line_id,
              camera_name,
              created_at,
              updated_at
            FROM qc.cameras
            WHERE line_id = :line_id
            ORDER BY channel_id
            """
        )
        result = await self.db.execute(stmt, {"line_id": line_id})
        rows = result.mappings().all()
        return [CameraOut(**dict(r)) for r in rows]

    async def get_camera_by_id(self, camera_id: int) -> CameraOut:
        row = await self._fetch_camera_row_by_id(camera_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Camera not found",
            )
        return CameraOut(**row)

    async def get_stream_url(self, camera_id: int) -> CameraStreamUrlOut:
        row = await self._fetch_camera_row_by_id(camera_id)
        if not row:
            raise HTTPException(status_code=404, detail="Camera not found for this channel")

        camera_ip = row["camera_ip"]
        channel_id = row["channel_id"]
        stream_name = self._ensure_ffmpeg_running_for(channel_id, camera_ip)
        hls_path = self._playlist_path(stream_name)

        max_attempts = max(1, int(_STREAM_READY_TIMEOUT_SECONDS / _STREAM_READY_POLL_SECONDS))
        for _ in range(max_attempts):
            proc = _ffmpeg_processes.get(channel_id)
            if proc is not None and proc.poll() is not None:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to start camera stream (RTSP unreachable or refused)",
                )

            if self._is_playlist_fresh(hls_path, _PLAYLIST_FRESHNESS_SECONDS):
                # Keep URL stable while the same FFmpeg process is alive.
                # This avoids unnecessary player re-initialization when clients re-call stream-url.
                stream_version = int(_ffmpeg_started_at.get(channel_id, time.time()))
                url = f"{settings.HLS_PUBLIC_BASE.rstrip('/')}/{stream_name}.m3u8?v={stream_version}"
                return CameraStreamUrlOut(url=url)

            await asyncio.sleep(_STREAM_READY_POLL_SECONDS)

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Camera did not respond in time",
        )
      
    def reset_focus(self):
        controller = CameraZoomController(
            camera_ip="192.168.10.18",
            username=settings.CAMERA_RTSP_USERNAME,
            password=settings.CAMERA_RTSP_PASSWORD,
        )
        out = controller.reset_focus()
        return CameraResetFocusOut(status=out)

    def _build_rtsp_url(self, camera_ip: str, channel_id: str) -> str:
        user = quote(settings.CAMERA_RTSP_USERNAME, safe="")
        pwd = quote(settings.CAMERA_RTSP_PASSWORD, safe="")
        rtsp_path = settings.CAMERA_RTSP_PATH.replace("{channel}", str(channel_id))

        return f"rtsp://{user}:{pwd}@{camera_ip}{rtsp_path}"

    def _playlist_path(self, stream_name: str) -> Path:
        return Path(settings.HLS_ROOT) / f"{stream_name}.m3u8"

    def _is_playlist_fresh(self, playlist_path: Path, max_age_seconds: float) -> bool:
        try:
            age_seconds = time.time() - playlist_path.stat().st_mtime
            return age_seconds <= max_age_seconds
        except FileNotFoundError:
            return False

    def _cleanup_hls_files(self, stream_name: str) -> None:
        hls_root = Path(settings.HLS_ROOT)
        hls_root.mkdir(parents=True, exist_ok=True)
        playlist_name = f"{stream_name}.m3u8"
        segment_prefix = f"{stream_name}_"

        for file_path in hls_root.iterdir():
            if not file_path.is_file():
                continue
            if file_path.name == playlist_name or (
                file_path.name.startswith(segment_prefix) and file_path.suffix == ".ts"
            ):
                try:
                    file_path.unlink()
                except FileNotFoundError:
                    continue
                except Exception as ex:
                    print(f"[HLS] failed to delete stale file {file_path}: {ex}")

    def _ensure_ffmpeg_running_for(self, channel_id: int, camera_ip: str) -> str:
        rtsp_url = self._build_rtsp_url(camera_ip, channel_id)
        stream_name = f"channel_{channel_id}"
        hls_output_path = self._playlist_path(stream_name)
        hls_segment_pattern = str(Path(settings.HLS_ROOT) / f"{stream_name}_%06d.ts")

        print(f"[HLS] HLS_ROOT = {settings.HLS_ROOT}, exists={os.path.isdir(settings.HLS_ROOT)}")
        print(f"[HLS] channel_id={channel_id}")
        print(f"[HLS] RTSP URL: {rtsp_url}")
        print(f"[HLS] HLS output: {hls_output_path}")

        with _ffmpeg_lock:
            proc = _ffmpeg_processes.get(channel_id)
            if proc is not None and proc.poll() is None:
                if self._is_playlist_fresh(hls_output_path, _PLAYLIST_FRESHNESS_SECONDS):
                    return stream_name

                started_at = _ffmpeg_started_at.get(channel_id, time.time())
                if (
                    not hls_output_path.exists()
                    and time.time() - started_at <= _PLAYLIST_FRESHNESS_SECONDS
                ):
                    # Fresh process can need a few seconds before first playlist is written.
                    return stream_name

                print(f"[FFmpeg] channel {channel_id} appears stale, restarting process")
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)

            self._cleanup_hls_files(stream_name)
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "info",
                "-rtsp_transport",
                "tcp",
                "-i",
                rtsp_url,
                # Transcode to H.264 so browsers can play it.
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-profile:v",
                "baseline",
                "-level",
                "3.1",
                "-crf",
                "28",
                "-g",
                "50",
                "-keyint_min",
                "50",
                "-sc_threshold",
                "0",
                "-an",
                "-f",
                "hls",
                "-hls_time",
                "6",
                "-hls_list_size",
                "10",
                "-hls_flags",
                "delete_segments+omit_endlist+independent_segments+temp_file",
                "-hls_segment_filename",
                hls_segment_pattern,
                str(hls_output_path),
            ]

            print("[FFmpeg] Command:", " ".join(cmd))

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

            # background thread to log stderr
            def _log_stderr(p: subprocess.Popen, ch: int) -> None:
                try:
                    for line in p.stderr:
                        print(f"[FFmpeg channel {ch}] {line.rstrip()}")
                except Exception as ex:
                    print(f"[FFmpeg channel {ch}] stderr read error: {ex}")

            threading.Thread(
                target=_log_stderr,
                args=(proc, channel_id),
                daemon=True,
            ).start()

            _ffmpeg_processes[channel_id] = proc
            _ffmpeg_started_at[channel_id] = time.time()
            print(f"[FFmpeg] Started for channel {channel_id}")

            return stream_name


class CameraZoomController:
    def __init__(self, camera_ip: str, username: str, password: str):
        self.camera_ip = camera_ip
        self.username = username
        self.md5_password = hashlib.md5(password.encode()).hexdigest()

    def zoom_in(self, autostop: str = '50') -> bool:
        return self._zoom('in', autostop)

    def zoom_out(self, autostop: str = '50') -> bool:
        return self._zoom('out', autostop)

    def _zoom(self, direction: str, autostop: str) -> bool:
        url = f'http://{self.camera_ip}/HAPI/V1.0/ptz_ctrl/zoom'
        params = {
            'direction': direction,
            'autostop': autostop,
            'username': self.username,
            'password': self.md5_password
        }
        try:
            requests.get(url, params=params)
            return True
        except:
            return False

    def reset_focus(self, delay: float = 0.3) -> bool:
        self.zoom_in()
        time.sleep(delay)
        self.zoom_out()
        return True

def get_service(db: AsyncSession = Depends(get_db)) -> CameraService:
    return CameraService(db)
