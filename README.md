# gen3d — a sensor driven realtime gcode server. 

itteration #1: A generative 3D printer that is self referencing.

The printer's own sound — the whine and groan of its stepper motors — is picked up by a microphone or vibration sensor on the ESP32-S3, streamed to the Pi, and fed back as the sculpting signal. High-frequency motor noise expands each layer outward; low, grinding movement contracts it. Silence produces no change. The object that emerges is a physical record of the machine's own voice during its making.

The second strand of the project is **CREEP** — *Cybernetic Reactive Errant Extruding Printer*: a wheeled chassis that wanders a space, galleries, possibly sprays a patch of floor with hairspray, prints a generated blob directly onto it, then moves on — leaving little sculptures scattered around like an animal that is pooping in the wild. Errant in every sense: straying from the correct path, making printing errors, depositing plastic where plastic has no business being.

---

## Hardware

| Component | Role |
|---|---|
| Ender 3 | Print engine |
| Raspberry Pi | Primary orchestrator |
| ESP32-S3 SuperMini | Sensor node (mic or vibration) |
| KY-038 microphone | Sound-reactive mode |
| MPU-6050 (×1 or ×2) | Vibration-reactive mode |
| NEMA17 stepper × 2 | Wheel drive (roaming mode) |
| DRV8825 drivers × 2 | Stepper control for wheels |
| Servo × 2 | Cam feet (stability) + aerosol trigger |
| Aerosol can (hairspray) | Floor adhesion |
| ESP32 (any) | Wheel/servo controller (roaming mode) |

---

## Repository layout

```
printer.py              Ender 3 serial driver — send G-code, block on 'ok'
gen3d_path.py           Per-point radius algorithm — the generative core
main.py                 Print orchestrator: heat, home, layer loop, sensor integration
viz_app.py              Flask web UI — live canvas, parameter controls, real-time sliders
mover.py                Robot locomotion abstraction (dry-run if no serial port given)
mission.py              Roaming mission loop — walk, park, spray, poop, repeat

gen3d_ear_v1.py              MicroPython — KY-038 mic, zero-crossing frequency → serial
gen3d_ear_arduino/           Arduino — KY-038 mic (same logic, C++)
gen3d_ear_mpu6050/           Arduino — single MPU-6050 vibration sensor
gen3d_ear_mpu6050_dual/      Arduino — dual MPU-6050 on shared I2C (X + Y motor vibration)
```

---

## How the generative shape works

Each layer is made of `n_points` (default 72) evenly-spaced points on a circle. For every point the sensor is sampled; the reading is mapped to a radius offset (positive = expand, negative = contract) clamped to a maximum overhang. The result is an irregular perimeter that is printed as a continuous extrusion. The previous layer's radii become the baseline for the next, so each layer inherits and deforms from the one below. The object grows as an integrated record of the sound environment over the full print duration.

---

## Wiring

### KY-038 microphone (MicroPython / gen3d_ear_v1.py)

```
KY-038 VCC  →  3V3
KY-038 GND  →  GND
KY-038 AO   →  GPIO1   (ADC1_CH0 on S3 SuperMini)
KY-038 DO   →  not connected
```

### Dual MPU-6050 vibration (gen3d_ear_mpu6050_dual)

Both sensors share one I2C bus; AD0 selects address.

```
Both sensors:  VCC→3V3  GND→GND  SCL→GPIO9  SDA→GPIO8
Sensor 1 (near X motor):  AD0→GND   (addr 0x68)
Sensor 2 (near Y motor):  AD0→3V3   (addr 0x69)
```

Mount sensors directly to the frame as close to each motor as practical.

---

## Setup

```bash
pip install pyserial flask
```

Flash the appropriate sensor sketch to the ESP32-S3 via Arduino IDE:
- **Sound**: `gen3d_ear_arduino/gen3d_ear_arduino.ino`
- **Vibration (single)**: `gen3d_ear_mpu6050/gen3d_ear_mpu6050.ino`
- **Vibration (dual)**: `gen3d_ear_mpu6050_dual/gen3d_ear_mpu6050_dual.ino`

Arduino IDE settings for ESP32-S3 SuperMini:
```
Board:            ESP32S3 Dev Module
USB CDC On Boot:  Enabled
USB Mode:         Hardware CDC and JTAG
```

---

## Usage

### Web UI (recommended)

```bash
python viz_app.py
# open http://raspberrypi.local:5000
```

Adjust parameters live, start/stop prints, watch the layer grow in the canvas.

### Command line

```bash
# dry run — generates G-code, sends nothing to printer
python main.py --dry-run --layers 40 --diameter 50

# real print — sound sensor
python main.py --sensor-source sound

# real print — CPU temp as fallback sensor
python main.py --sensor-source cpu
```

---

## CREEP — Cybernetic Reactive Errant Extruding Printer

The robot wanders a floor area, parking at each location to print a small blob directly onto the surface, then moves on.

```bash
# full dry run — no hardware needed
python mission.py --poops 5 --mode wander

# real robot, real printer
python mission.py \
  --mover   /dev/ttyUSB0 \   # ESP32 wheel controller
  --printer /dev/ttyACM0 \   # Ender 3
  --poops   8 \
  --mode    wander \
  --area    2000             # roam radius in mm
```

**Location modes**

| Mode | Behaviour |
|---|---|
| `random` | Scatter points uniformly within the roam area |
| `grid` | Regular grid clipped to a circle |
| `wander` | Each step biased from the last — natural animal movement |

**Per-poop sequence**
1. Drive to location (dead-reckoned via stepper counts)
2. Deploy cam feet — body settles onto floor, wheels lift clear
3. Spray hairspray patch — robot wiggles for coverage
4. Print blob — heat-up time doubles as hairspray tack time
5. Retract feet, move on

### ESP32 wheel controller protocol

`mover.py` talks to the wheel ESP32 over serial with plain text commands:

```
MOVE 400.0    forward N mm, reply OK when done
TURN 90.0     rotate N degrees (+ clockwise), reply OK
PARK          deploy cam feet, reply OK
UNPARK        retract feet, reply OK
SPRAY 2000    trigger aerosol for N ms, reply OK
```

The firmware for this is a short Arduino sketch (not yet written — the chassis is still being built).

---

## Project status

| Component | Status |
|---|---|
| Generative print engine | Working |
| Sound sensor (KY-038) | Working |
| Vibration sensor (MPU-6050 dual) | Working |
| Web UI | Working |
| `mover.py` / `mission.py` | Written, untested — chassis not yet built |
| Wheel ESP32 firmware | Not yet written |
| Physical chassis | Scrounging parts |
