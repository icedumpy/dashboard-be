import os
import subprocess
import threading
from typing import Dict, List, Optional
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
_ffmpeg_lock = threading.Lock()


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
      channel_id = row['channel_id']

      stream_name = self._ensure_ffmpeg_running_for(channel_id, camera_ip)

      hls_path = f"./hls/{stream_name}.m3u8"

      max_attempts = 10
      for _ in range(max_attempts):
          if os.path.exists(hls_path):
              url = f"{settings.HLS_PUBLIC_BASE}/{stream_name}.m3u8"
              return CameraStreamUrlOut(url=url)

          proc = _ffmpeg_processes.get(channel_id)
          if proc is not None and proc.poll() is not None:
              raise HTTPException(
                  status_code=status.HTTP_502_BAD_GATEWAY,
                  detail="Failed to start camera stream (RTSP unreachable or refused)",
              )

          await asyncio.sleep(0.5)

      raise HTTPException(
          status_code=status.HTTP_504_GATEWAY_TIMEOUT,
          detail="Camera did not respond in time",
      )
      
    def reset_focus(self):
      controller = CameraZoomController(
        camera_ip='192.168.10.18',
        username=settings.CAMERA_RTSP_USERNAME,
        password=settings.CAMERA_RTSP_PASSWORD
      )
      out = controller.reset_focus()
      return CameraResetFocusOut(status=out)
      
    def _build_rtsp_url(self, camera_ip: str, channel_id: str) -> str:
      user = quote(settings.CAMERA_RTSP_USERNAME, safe="")
      pwd = quote(settings.CAMERA_RTSP_PASSWORD, safe="")
      rtsp_path = settings.CAMERA_RTSP_PATH.replace('{channel}', str(channel_id))

      return f"rtsp://{user}:{pwd}@{camera_ip}{rtsp_path}"


    def _ensure_ffmpeg_running_for(self, channel_id: int, camera_ip: str) -> str:
      rtsp_url = self._build_rtsp_url(camera_ip, channel_id)

      stream_name = f"channel_{channel_id}"
      hls_output_path = f"./hls/{stream_name}.m3u8"
      output_path = hls_output_path
      print("hls_output_path => ", output_path)

      print(f"[HLS] HLS_ROOT = {settings.HLS_ROOT}, exists={os.path.isdir(settings.HLS_ROOT)}")
      print(f"[HLS] channel_id={channel_id}")
      print(f"[HLS] RTSP URL: {rtsp_url}")
      print(f"[HLS] HLS output: {output_path}")

      with _ffmpeg_lock:
          proc = _ffmpeg_processes.get(channel_id)
          if proc is not None and proc.poll() is None:
              return stream_name

          cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "info",

                # RTSP robustness / timeouts (important for long running)
                "-rtsp_transport", "tcp",
                "-stimeout", "30000000",        # 30s (microseconds)
                # "-rw_timeout", "30000000",      # 30s (microseconds)

                # Timestamp sanity (prevents weird “past date” behavior on discontinuity)
                "-fflags", "+genpts",
                "-use_wallclock_as_timestamps", "1",

                "-i", rtsp_url,

                # Video encode
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-profile:v", "baseline",
                "-level", "3.1",

                # Rate control (CRF is ok; add maxrate/bufsize if you want more stability)
                "-crf", "28",

                # GOP aligned with hls_time (2s segments; pick fps-based GOP if you know fps)
                "-g", "50",
                "-keyint_min", "50",
                "-sc_threshold", "0",

                "-an",

                # HLS output
                "-f", "hls",
                "-hls_time", "2",
                "-hls_list_size", "10",
                "-hls_allow_cache", "0",
                "-hls_flags", "delete_segments+omit_endlist+independent_segments+temp_file",

                # IMPORTANT: stable segment filenames + wrap so it never grows forever
                "-hls_segment_filename", f"./hls/{stream_name}_%06d.ts",
                "-hls_wrap", "1000",

                hls_output_path,
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
              except Exception as e:
                  print(f"[FFmpeg channel {ch}] stderr read error: {e}")

          threading.Thread(
              target=_log_stderr,
              args=(proc, channel_id),
              daemon=True,
          ).start()

          _ffmpeg_processes[channel_id] = proc
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
