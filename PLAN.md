# Plan: Arduino M63 RC Car Controller (vroomba)

## TL;DR
Port the M63 RC protocol (from goebish's XN297 gist) onto an Arduino Nano + NRF24L01 (using the XN297 emulation layer from nrf24_multipro), then build a Python serial controller for keyboard-driven car control.

## Architecture

```
[Python on Mac] --USB/Serial--> [Arduino Nano] --SPI--> [NRF24L01] ~~2.4GHz~~ [Car's XN297 receiver]
```

The M63 gist talks directly to a real XN297 chip via Raspberry Pi SPI. We re-target it to use the NRF24L01 + XN297 emulation layer (scrambling, CRC, bit-reversal, preamble injection) from nrf24_multipro.

### Source References
- **M63 protocol gist**: https://gist.github.com/goebish/b5568616fa6183ba1e25 — `m63.cpp` for RPi + XN297
- **nrf24_multipro repo**: https://github.com/goebish/nrf24_multipro — NRF24L01 driver + XN297 emulation layer for Arduino

---

## Phase 1: Environment Setup

1. **Install Arduino IDE 2.x** from https://www.arduino.cc/en/software (recommended for first-time — GUI for board/port selection). Alternatively, `brew install arduino-cli` + `arduino-cli core install arduino:avr`.
2. **Connect Arduino Nano via USB** — port will appear as `/dev/cu.usbserial-*` or `/dev/cu.wchusbserial-*`. Many Nano clones use the CH340G chip — if it doesn't show up, install the CH340 driver (`brew install --cask wch-ch34x-usb-serial-driver`).
3. **Verify Arduino connection** — upload the built-in Blink sketch. In IDE: File > Examples > 01.Basics > Blink. Select board "Arduino Nano", processor "ATmega328P" (try "Old Bootloader" variant if upload fails). LED on pin 13 should blink.
4. **Install Python deps** — `pip install pyserial pynput`

## Phase 2: Hardware Wiring + Verification

### Step 2a: Wiring
Wire NRF24L01 (via adapter board) to Arduino Nano matching the nrf24_multipro pinout. The adapter board handles voltage regulation and decoupling.

| NRF24L01 Pin | Arduino Pin | Notes |
|---|---|---|
| VCC | 5V (adapter regulates to 3.3V) | Adapter handles this |
| GND | GND | |
| CE | D9 | Chip Enable |
| CSN | D10 | Chip Select |
| MOSI | D11 | SPI Data Out |
| MISO | D12 | SPI Data In |
| SCK | D13 | SPI Clock |
| IRQ | (not connected) | Not needed |

> Uses **bit-banged SPI** on the standard Arduino SPI pins (D11-D13). All signal pins are on the "Digital" header (D9-D13), grouped together for easy wiring. Port manipulation uses PORTB.

### Step 2b: Verify NRF24L01 Connection
Before writing any M63 code, upload a minimal test sketch (`nrf24_test/nrf24_test.ino`) that:
1. Initializes SPI pins and bit-bangs a read of the NRF24L01 STATUS register (0x07)
2. A fresh/reset NRF24L01 always returns `0x0E` from this register
3. Also writes a test value to a writable register (e.g., RF_CH) and reads it back to confirm bidirectional SPI
4. Reports results over Serial — "NRF24L01 detected, SPI OK" or "SPI FAIL: got 0xXX (expected 0x0E)"

If you get `0x00` or `0xFF`, the wiring is wrong (or module not powered). Takes 2 minutes and saves hours of debugging later.

## Phase 3: Arduino Firmware

### Step 3a: Infrastructure files from nrf24_multipro

**Decision: Copy files, don't clone/fork.**
- We only need 4 of ~15 files. The repo is itself an Arduino sketch project, so having it as a subfolder would confuse the Arduino IDE.
- The repo hasn't been updated meaningfully in 7+ years — no upstream changes coming.
- GPL-3.0 license headers are already in every file — attribution preserved.

Files copied verbatim into `m63_controller/`:
- `iface_nrf24l01.h` — register enums, bit mnemonics, TX power enum
- `softSPI.ino` — bit-banged SPI implementation
- `nRF24L01.ino` — NRF24L01 register-level driver
- `XN297_emu.ino` — XN297 emulation layer (scrambling, CRC, preamble)

### Step 3b: Port M63 protocol → `M63.ino`
Translate the gist's raw XN297 SPI commands into the nrf24_multipro abstraction layer:

- **`M63_init()`**: Configure NRF24L01 via emulation layer. Skip XN297-specific debug registers (BB_CAL, RF_CAL, DEMOD_CAL) — not applicable to NRF24L01.
- **`M63_bind()`**: 128 iterations sending bind packets via `XN297_WritePayload()` on frequency 0x2D. Then switch TX address to transmitter ID.
- **`M63_send_packet()`**: Build 9-byte packet with mystery_byte sequence and rolling checksum, channel-hop using the 24-entry table.

### Step 3c: Main sketch → `m63_controller.ino`
- `setup()`: Pin modes, serial init, NRF24L01 init, M63 bind
- `loop()`: Read serial commands, send RF packets at ~263Hz (3800µs period)

### Serial Protocol (Arduino ↔ Python)
Binary, 4 bytes per command:
- Byte 0: `0x55` (sync/start marker)
- Byte 1: throttle (0-255, 128 = neutral/stop)
- Byte 2: rudder/steering (0-255, 128 = centered)
- Byte 3: XOR checksum of bytes 0-2

Arduino sends back: `0x01` = bound OK, `0x02` = binding, `0xFF` = error.

## Phase 4: Python Controller (`controller.py`)

- Serial connection at 115200 baud, waits for bind-complete byte
- Keyboard via `pynput`: Up/Down = throttle, Left/Right = steering, Space = emergency stop
- Control loop at ~50Hz
- Key release ramps back to neutral

## Phase 5: Test & Debug

1. Compile sketch for Arduino Nano — verify no errors
2. Upload, check serial monitor for "Binding..." / "Bound OK"
3. Power on car, reset Arduino — should bind within a few seconds
4. Run `python controller.py` — arrow keys control the car
5. If no bind: verify NRF24L01 SPI (Phase 2b test), try different TX power, check protocol variant

---

## File Structure

```
vroomba/
├── PLAN.md                          ← this document
├── nrf24_test/                      ← Phase 2b: SPI wiring verification sketch
│   ├── nrf24_test.ino
│   ├── softSPI.ino                  (from nrf24_multipro)
│   └── iface_nrf24l01.h            (from nrf24_multipro)
├── m63_controller/                  ← Phase 3: main Arduino sketch
│   ├── m63_controller.ino          (new — setup/loop, serial command parsing)
│   ├── M63.ino                     (new — ported M63 protocol)
│   ├── iface_nrf24l01.h            (from nrf24_multipro)
│   ├── softSPI.ino                 (from nrf24_multipro)
│   ├── nRF24L01.ino                (from nrf24_multipro)
│   └── XN297_emu.ino               (from nrf24_multipro)
└── controller.py                    ← Phase 4: Python keyboard controller
```

## Decisions & Assumptions

- **Copy, don't clone** nrf24_multipro: Only 4 files needed, repo is frozen, avoids IDE confusion. GPL-3.0 headers preserved.
- **Adapter board**: Handles voltage regulation + decoupling. VCC can be sourced from 5V.
- **Transmitter ID**: Using stock TX ID from the gist (`{0xE2, 0x4D, 0x3C}`). Car likely accepts any ID during binding, but known-good minimizes variables.
- **Bit-banged SPI**: Software SPI (not hardware) — proven approach from nrf24_multipro, avoids pin conflicts.
- **Car controls**: Throttle + rudder(steering) only. Aileron/elevator sent as neutral.
- **No telemetry**: M63 is TX-only, no acknowledgment from car.

## Further Considerations

1. **CH340 driver** — if the Nano doesn't appear as a serial port on macOS, install the CH340 driver first. Very common with clones.
2. **Protocol variant risk** — the M63 gist is ~11 years old. If the Jada car uses a newer firmware/protocol variant, binding may not work. Fallback: capture packets from the stock transmitter with an SDR or logic analyzer to verify the protocol matches.
3. **Future: voice/AI control** — once keyboard control works, this serial protocol makes it straightforward to swap `pynput` keyboard input for a speech-to-intent pipeline (e.g., Whisper → LLM → control commands). The Arduino firmware stays unchanged.
