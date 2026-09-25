/*
 * Remote Hijack — drive RC car by actuating the remote's switches
 * via optocouplers (PC817) connected to the back of the PCB.
 *
 * 4 digital outputs → 4 optocouplers → 4 switches:
 *   D2 = Forward
 *   D3 = Reverse
 *   D4 = Left
 *   D5 = Right
 *
 * Serial protocol (115200 baud):
 *   Single-byte commands:
 *     'F' = forward ON     'f' = forward OFF
 *     'R' = reverse ON     'r' = reverse OFF
 *     'L' = left ON        'l' = left OFF
 *     'G' = right ON       'g' = right OFF
 *     'X' = all OFF (emergency stop)
 *     '?' = report current state
 *
 *   Also accepts a compact bitmask:
 *     '!' + 1 byte (bits: 0=FWD, 1=REV, 2=LEFT, 3=RIGHT)
 *     e.g. '!' 0x05 = forward + left
 *
 * At startup, runs a self-test toggling each output for 300ms.
 */

#define PIN_FWD   2
#define PIN_REV   3
#define PIN_LEFT  4
#define PIN_RIGHT 5

#define NUM_PINS 4
const uint8_t pins[NUM_PINS] = {PIN_FWD, PIN_REV, PIN_LEFT, PIN_RIGHT};
const char* names[NUM_PINS] = {"FWD", "REV", "LEFT", "RIGHT"};

void set_all(bool state)
{
    for (uint8_t i = 0; i < NUM_PINS; i++)
        digitalWrite(pins[i], state ? HIGH : LOW);
}

void report_state()
{
    Serial.print(F("State: "));
    for (uint8_t i = 0; i < NUM_PINS; i++) {
        if (digitalRead(pins[i])) {
            Serial.print(names[i]);
            Serial.print(' ');
        }
    }
    if (!digitalRead(PIN_FWD) && !digitalRead(PIN_REV) &&
        !digitalRead(PIN_LEFT) && !digitalRead(PIN_RIGHT)) {
        Serial.print(F("IDLE"));
    }
    Serial.println();
}

void setup()
{
    Serial.begin(115200);

    for (uint8_t i = 0; i < NUM_PINS; i++) {
        pinMode(pins[i], OUTPUT);
        digitalWrite(pins[i], LOW);
    }

    Serial.println(F("\n=== Remote Hijack ==="));
    Serial.println(F("Self-test: toggling each output..."));

    // Toggle each output so you can verify the car responds
    for (uint8_t i = 0; i < NUM_PINS; i++) {
        Serial.print(F("  "));
        Serial.print(names[i]);
        Serial.println(F(" ON"));
        digitalWrite(pins[i], HIGH);
        delay(300);
        digitalWrite(pins[i], LOW);
        Serial.print(F("  "));
        Serial.print(names[i]);
        Serial.println(F(" OFF"));
        delay(200);
    }

    Serial.println(F("Self-test done."));
    Serial.println(F("Commands: F/f R/r L/l G/g X ? (or ! + bitmask)"));
    Serial.println(F("Ready."));
}

void loop()
{
    if (!Serial.available()) return;

    char c = Serial.read();

    switch (c) {
        case 'F': digitalWrite(PIN_FWD, HIGH);   break;
        case 'f': digitalWrite(PIN_FWD, LOW);    break;
        case 'R': digitalWrite(PIN_REV, HIGH);   break;
        case 'r': digitalWrite(PIN_REV, LOW);    break;
        case 'L': digitalWrite(PIN_LEFT, HIGH);  break;
        case 'l': digitalWrite(PIN_LEFT, LOW);   break;
        case 'G': digitalWrite(PIN_RIGHT, HIGH); break;
        case 'g': digitalWrite(PIN_RIGHT, LOW);  break;
        case 'X': set_all(false);                break;
        case '?': report_state();                return;
        case '!': {
            // Wait for bitmask byte
            uint32_t t0 = millis();
            while (!Serial.available()) {
                if (millis() - t0 > 100) return;  // timeout
            }
            uint8_t mask = Serial.read();
            digitalWrite(PIN_FWD,   (mask & 0x01) ? HIGH : LOW);
            digitalWrite(PIN_REV,   (mask & 0x02) ? HIGH : LOW);
            digitalWrite(PIN_LEFT,  (mask & 0x04) ? HIGH : LOW);
            digitalWrite(PIN_RIGHT, (mask & 0x08) ? HIGH : LOW);
            break;
        }
        default: return;  // ignore unknown
    }

    report_state();
}
