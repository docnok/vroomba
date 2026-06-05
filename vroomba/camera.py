"""Camera capture manager — background-thread frame grabbing via OpenCV."""

import base64
import json as _json
import logging
import platform
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError

import cv2

from vroomba.config import settings

log = logging.getLogger(__name__)


class CameraManager:
    """Grabs frames from a webcam in a background thread, serves latest JPEG."""

    #TODO: what happens when a camera isn't plugged in? seems like we sometimes get the expected error (resulting in "no signal" in the UI), but other times we get a frame with a gear in it, suggesting some sort of predefined input. How to tell the difference when no camera is available?
    def __init__(
        self,
        source: int | str = settings.camera_mjpeg_url,
        width: int = settings.camera_width,
        height: int = settings.camera_height,
        fps: int = settings.camera_fps,
        jpeg_quality: int = settings.camera_jpeg_quality,
    ):
        self._source = source
        self._width = width
        self._height = height
        self._fps = fps
        self._jpeg_quality = jpeg_quality

        self._cap: cv2.VideoCapture | None = None
        self._stream = None  # HTTP response for MJPEG path
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False

        self._frame_jpeg: bytes | None = None  # latest JPEG-encoded frame

    # -- lifecycle -------------------------------------------------------------

    @property
    def _is_network_source(self) -> bool:
        return isinstance(self._source, str) and self._source.startswith(("http://", "https://"))

    def start(self) -> bool:
        """Open the camera and start the capture thread. Returns True on success."""
        if self._is_network_source:
            return self._start_mjpeg()
        return self._start_webcam()

    def _start_webcam(self) -> bool:
        """Open a local webcam via OpenCV."""
        log.info("Opening webcam source: %r", self._source)
        cap = cv2.VideoCapture(self._source)
        if not cap.isOpened():
            log.error("Camera source %r could not be opened", self._source)
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)

        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop_webcam, daemon=True)
        self._thread.start()

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log.info("Webcam started: source=%r  %dx%d", self._source, actual_w, actual_h)
        return True

    def _start_mjpeg(self) -> bool:
        """Open an HTTP MJPEG stream directly (no OpenCV decode)."""
        if not self._probe_network_source(self._source):
            log.warning("Camera source %r is unreachable (fast-fail)", self._source)
            return False

        log.info("Opening MJPEG stream: %r", self._source)
        try:
            req = Request(self._source)
            resp = urlopen(req, timeout=3.0)  # noqa: S310 — URL validated by probe
        except (URLError, OSError) as exc:
            log.error("Failed to open MJPEG stream %r: %s", self._source, exc)
            return False

        self._stream = resp
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop_mjpeg, daemon=True)
        self._thread.start()
        log.info("MJPEG stream started: %s", self._source)
        return True

    def stop(self) -> None:
        """Stop the capture thread and release the camera."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._frame_jpeg = None
        log.info("Camera stopped")

    @property
    def is_running(self) -> bool:
        return self._running and (self._cap is not None or self._stream is not None)

    @property
    def resolution(self) -> tuple[int, int] | None:
        if self._cap and self._cap.isOpened():
            return (
                int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
        return None

    @property
    def source_id(self) -> str:
        """Stable ID for currently selected source used by UI/API."""
        if isinstance(self._source, int):
            return f"cam:{self._source}"
        return "mjpeg:local"

    # -- frame access ----------------------------------------------------------

    def get_frame(self) -> bytes | None:
        """Return the latest JPEG-encoded frame, or None if unavailable."""
        with self._lock:
            return self._frame_jpeg

    def get_frame_b64(self) -> str | None:
        """Return the latest frame as a base64-encoded JPEG string."""
        frame = self.get_frame()
        if frame is None:
            return None
        return base64.b64encode(frame).decode("ascii")

    # -- internal --------------------------------------------------------------

    def _capture_loop_webcam(self) -> None:
        """Continuously grab frames from a local webcam via OpenCV."""
        interval = 1.0 / self._fps
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(interval)
                continue

            ok, buf = cv2.imencode(".jpg", frame, encode_params)
            if ok:
                with self._lock:
                    self._frame_jpeg = buf.tobytes()

            time.sleep(interval)

    def _capture_loop_mjpeg(self) -> None:
        """Read raw JPEG frames from an MJPEG boundary stream (no decode/re-encode)."""
        buf = b""
        while self._running:
            try:
                chunk = self._stream.read(4096)
            except OSError:
                log.warning("MJPEG stream read error — stopping")
                break
            if not chunk:
                log.warning("MJPEG stream ended")
                break
            buf += chunk
            # MJPEG frames are delimited by JPEG SOI (FFD8) and EOI (FFD9) markers
            while True:
                soi = buf.find(b"\xff\xd8")
                if soi == -1:
                    buf = buf[-1:]  # keep last byte in case of split marker
                    break
                eoi = buf.find(b"\xff\xd9", soi + 2)
                if eoi == -1:
                    break  # need more data
                frame = buf[soi : eoi + 2]
                buf = buf[eoi + 2 :]
                with self._lock:
                    self._frame_jpeg = frame
        self._running = False

    @staticmethod
    def _probe_network_source(url: str) -> bool:
        """Fast host:port reachability check for network cameras."""
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False

        if parsed.port:
            port = parsed.port
        elif parsed.scheme == "https":
            port = 443
        else:
            port = 80

        try:
            with socket.create_connection(
                (host, port),
                timeout=settings.camera_connect_timeout_seconds,
            ):
                return True
        except OSError:
            return False

    # -- device enumeration ----------------------------------------------------

    @staticmethod
    def enumerate_webcams(max_index: int = 4) -> list[dict]:
        """Probe indices 0‥max_index-1 in parallel and return [{index, name}] for working cameras."""
        threads: list[tuple[int, threading.Thread, list]] = []
        for i in range(max_index):
            result = [False]

            def _try(idx=i, res=result):
                cap = cv2.VideoCapture(idx)
                res[0] = cap.isOpened()
                cap.release()

            t = threading.Thread(target=_try, daemon=True)
            t.start()
            threads.append((i, t, result))

        valid: list[int] = []
        for idx, t, result in threads:
            t.join(timeout=2.0)
            if result[0]:
                valid.append(idx)

        names = CameraManager._get_device_names(len(valid))
        return [
            {"index": idx, "name": names.get(pos, f"Camera {idx}")}
            for pos, idx in enumerate(valid)
        ]

    @staticmethod
    def list_sources() -> list[dict]:
        """Return selectable sources including local webcams and configured MJPEG stream."""
        webcams = CameraManager.enumerate_webcams()
        sources = [
            {
                "id": f"cam:{cam['index']}",
                "name": cam["name"],
                "type": "webcam",
                "description": f"index {cam['index']}",
            }
            for cam in webcams
        ]
        sources.append(
            {
                "id": "mjpeg:local",
                "name": settings.camera_mjpeg_name,
                "type": "mjpeg",
                "description": f"Wi-Fi MJPEG stream ({settings.camera_mjpeg_url})",
            }
        )
        return sources

    @staticmethod
    def _get_device_names(count: int) -> dict[int, str]:
        """Return position→name mapping using platform APIs (best-effort)."""
        names: dict[int, str] = {}
        try:
            system = platform.system()
            if system == "Darwin":
                raw = subprocess.check_output(
                    ["system_profiler", "SPCameraDataType", "-json"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
                data = _json.loads(raw)
                for pos, cam in enumerate(data.get("SPCameraDataType", [])):
                    names[pos] = cam.get("_name", f"Camera {pos}")
            elif system == "Linux":
                for pos, dev_path in enumerate(
                    sorted(Path("/sys/class/video4linux").glob("video*"))
                ):
                    name_file = dev_path / "name"
                    if name_file.exists():
                        names[pos] = name_file.read_text().strip()
        except Exception:
            pass
        return names

    # -- live switching --------------------------------------------------------

    def switch(self, source: int | str) -> bool:
        """Stop the current capture and restart on *source*. Returns True on success."""
        log.info("Switching camera to source %r", source)
        self.stop()
        self._source = source
        return self.start()

    @staticmethod
    def source_from_id(source_id: str) -> int | str:
        """Resolve API source IDs into OpenCV VideoCapture sources."""
        if source_id.startswith("cam:"):
            return int(source_id.split(":", 1)[1])
        if source_id == "mjpeg:local":
            return settings.camera_mjpeg_url
        raise ValueError(f"Unsupported camera source ID: {source_id}")
