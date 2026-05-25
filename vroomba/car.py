"""Serial interface to the Arduino running remote_hijack.ino."""

import glob
import logging
import threading
import time

import serial

from vroomba.models import ControlCommand

log = logging.getLogger(__name__)


def find_serial_port() -> str | None:
    """Auto-detect Arduino serial port on macOS."""
    for pattern in (
        "/dev/cu.usbserial-*",
        "/dev/cu.wchusbserial-*",
        "/dev/cu.usbmodem-*",
        "/dev/cu.SLAB_USBtoUART*",
    ):
        ports = glob.glob(pattern)
        if ports:
            return ports[0]
    all_ports = glob.glob("/dev/cu.*")
    usb_ports = [
        p for p in all_ports if "Bluetooth" not in p and p != "/dev/cu.wlan-debug"
    ]
    return usb_ports[0] if usb_ports else None


class CarInterface:
    """Thread-safe serial connection to the RC car via Arduino."""

    def __init__(self, port: str = "auto", baud: int = 115200):
        self._port_spec = port
        self._baud = baud
        self._ser: serial.Serial | None = None
        self._lock = threading.Lock()

    # -- connection lifecycle --------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def connect(self) -> bool:
        """Open serial port and wait for Arduino 'Ready.' message."""
        port = self._port_spec
        if port == "auto":
            port = find_serial_port()
            if port is None:
                log.error("No serial port found")
                return False

        try:
            self._ser = serial.Serial(port, self._baud, timeout=0.1)
        except serial.SerialException as e:
            log.error("Serial open failed: %s", e)
            self._ser = None
            return False

        log.info("Opened %s @ %d", port, self._baud)
        return self._wait_for_ready()

    def disconnect(self) -> None:
        if self._ser and self._ser.is_open:
            self.idle()
            time.sleep(0.05)
            self.idle()
            self._ser.close()
            log.info("Serial port closed")
        self._ser = None

    # -- control ---------------------------------------------------------------

    def set_control(self, cmd: ControlCommand) -> None:
        """Send a ControlCommand to the Arduino as bitmask."""
        mask = 0
        if cmd.throttle == "fwd":
            mask |= 0x01  # BIT_FWD
        elif cmd.throttle == "rev":
            mask |= 0x02  # BIT_REV
        if cmd.steering == "left":
            mask |= 0x04  # BIT_LEFT
        elif cmd.steering == "right":
            mask |= 0x08  # BIT_RIGHT
        self._send_mask(mask)

    def idle(self) -> None:
        """All controls to neutral."""
        self._send_mask(0x00)

    # -- internals -------------------------------------------------------------

    def _send_mask(self, mask: int) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.write(b"!" + bytes([mask & 0x0F]))

    def _wait_for_ready(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ser and self._ser.in_waiting:
                line = self._ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    log.info("Arduino: %s", line)
                if "Ready" in line:
                    return True
            else:
                time.sleep(0.05)
        log.warning("Timeout waiting for Arduino ready")
        self._ser.close()
        self._ser = None
        return False
