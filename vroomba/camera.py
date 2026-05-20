"""Camera capture manager — background-thread frame grabbing via OpenCV."""

import base64
import logging
import threading
import time

import cv2

from vroomba.config import settings

log = logging.getLogger(__name__)


class CameraManager:
    """Grabs frames from a webcam in a background thread, serves latest JPEG."""

    def __init__(
        self,
        index: int = settings.camera_index,
        width: int = settings.camera_width,
        height: int = settings.camera_height,
        fps: int = settings.camera_fps,
        jpeg_quality: int = settings.camera_jpeg_quality,
    ):
        self._index = index
        self._width = width
        self._height = height
        self._fps = fps
        self._jpeg_quality = jpeg_quality

        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False

        self._frame_jpeg: bytes | None = None  # latest JPEG-encoded frame

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> bool:
        """Open the camera and start the capture thread. Returns True on success."""
        cap = cv2.VideoCapture(self._index)
        if not cap.isOpened():
            log.error("Camera index %d could not be opened", self._index)
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)

        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log.info("Camera started: index=%d  %dx%d", self._index, actual_w, actual_h)
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
        self._frame_jpeg = None
        log.info("Camera stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._cap is not None

    @property
    def resolution(self) -> tuple[int, int] | None:
        if self._cap and self._cap.isOpened():
            return (
                int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
        return None

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

    def _capture_loop(self) -> None:
        """Continuously grab frames in a background thread."""
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
