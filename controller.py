#!/usr/bin/env python3
"""
vroomba controller — keyboard control for RC car via remote hijack

Sends digital switch commands over serial to the Arduino,
which actuates optocouplers connected to the RC remote's switches.

Controls:
    Up arrow    — forward
    Down arrow  — reverse
    Left arrow  — left
    Right arrow — right
    Space       — emergency stop (all off)
    Q / Esc     — quit

The remote's switches are digital (on/off), so controls are
direct: key held = switch closed, key released = switch open.

Usage:
    python controller.py [serial_port]
    python controller.py              # auto-detect port
    python controller.py /dev/cu.usbserial-A5069RR4
"""

import sys
import time
import glob
import threading

import serial
from pynput import keyboard


# ---- Configuration ----
BAUD_RATE = 115200
SEND_HZ = 20  # how often we push state to Arduino

# Bitmask bits (must match remote_hijack.ino)
BIT_FWD   = 0x01
BIT_REV   = 0x02
BIT_LEFT  = 0x04
BIT_RIGHT = 0x08


def find_serial_port():
    """Auto-detect Arduino serial port on macOS."""
    patterns = [
        "/dev/cu.usbserial-*",
        "/dev/cu.wchusbserial-*",
        "/dev/cu.usbmodem-*",
        "/dev/cu.SLAB_USBtoUART*",
    ]
    for pattern in patterns:
        ports = glob.glob(pattern)
        if ports:
            return ports[0]
    all_ports = glob.glob("/dev/cu.*")
    usb_ports = [p for p in all_ports if "Bluetooth" not in p and p != "/dev/cu.wlan-debug"]
    if usb_ports:
        return usb_ports[0]
    return None


class CarController:
    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD_RATE, timeout=0.1)
        self.running = True
        self.keys_held = set()
        self.lock = threading.Lock()
        self.last_mask = None  # track to avoid redundant sends

    def wait_for_ready(self):
        """Read Arduino startup output until 'Ready.' appears."""
        print("Waiting for Arduino...")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.ser.in_waiting:
                line = self.ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    print(f"  [{line}]")
                if "Ready" in line:
                    return True
            else:
                time.sleep(0.05)
        print("Timeout waiting for Arduino. Proceeding anyway.")
        return True

    def on_key_press(self, key):
        with self.lock:
            if key == keyboard.Key.up:
                self.keys_held.add("fwd")
                self.keys_held.discard("rev")  # can't be fwd+rev
            elif key == keyboard.Key.down:
                self.keys_held.add("rev")
                self.keys_held.discard("fwd")
            elif key == keyboard.Key.left:
                self.keys_held.add("left")
                self.keys_held.discard("right")
            elif key == keyboard.Key.right:
                self.keys_held.add("right")
                self.keys_held.discard("left")
            elif key == keyboard.Key.space:
                self.keys_held.clear()
            elif key == keyboard.Key.esc:
                self.running = False
            elif hasattr(key, "char") and key.char == "q":
                self.running = False

    def on_key_release(self, key):
        with self.lock:
            if key == keyboard.Key.up:
                self.keys_held.discard("fwd")
            elif key == keyboard.Key.down:
                self.keys_held.discard("rev")
            elif key == keyboard.Key.left:
                self.keys_held.discard("left")
            elif key == keyboard.Key.right:
                self.keys_held.discard("right")

    def build_mask(self):
        """Build bitmask from currently held keys."""
        mask = 0
        with self.lock:
            if "fwd" in self.keys_held:
                mask |= BIT_FWD
            if "rev" in self.keys_held:
                mask |= BIT_REV
            if "left" in self.keys_held:
                mask |= BIT_LEFT
            if "right" in self.keys_held:
                mask |= BIT_RIGHT
        return mask

    def send_state(self, mask):
        """Send bitmask command to Arduino."""
        self.ser.write(b"!" + bytes([mask]))
        self.last_mask = mask

    def display_status(self, mask):
        """Show current state."""
        parts = []
        if mask & BIT_FWD:   parts.append("FWD")
        if mask & BIT_REV:   parts.append("REV")
        if mask & BIT_LEFT:  parts.append("LEFT")
        if mask & BIT_RIGHT: parts.append("RIGHT")
        state = " + ".join(parts) if parts else "IDLE"
        print(f"\r  [{state:<25s}]", end="", flush=True)

    def drain_serial(self):
        """Read and discard Arduino responses to avoid buffer buildup."""
        while self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    def run(self):
        """Main control loop."""
        print("\n--- vroomba controller ---")
        print("  Up/Down  = forward/reverse")
        print("  Left/Right = steer")
        print("  Space = stop   Q/Esc = quit\n")

        listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release,
        )
        listener.start()

        interval = 1.0 / SEND_HZ
        try:
            while self.running:
                t0 = time.monotonic()
                mask = self.build_mask()
                # Always send periodically (keeps connection alive),
                # but display only on change
                self.send_state(mask)
                self.display_status(mask)
                self.drain_serial()
                elapsed = time.monotonic() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except KeyboardInterrupt:
            pass
        finally:
            # All off on exit
            self.send_state(0x00)
            time.sleep(0.05)
            self.send_state(0x00)
            listener.stop()
            self.ser.close()
            print("\n\nDisconnected. All switches off.")


def main():
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        port = find_serial_port()
        if port is None:
            print("ERROR: No serial port found. Connect Arduino and try again,")
            print("       or specify port: python controller.py /dev/cu.usbserial-XXXX")
            sys.exit(1)

    print(f"Connecting to {port}...")
    ctrl = CarController(port)

    # Give Arduino time to reset after serial open
    time.sleep(2)
    ctrl.wait_for_ready()
    ctrl.run()


if __name__ == "__main__":
    main()
