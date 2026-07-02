# gen3d — realtime generative 3D printing project

## What this is
A live, sensor-driven gcode generation loop. Each printed layer's path deviates
from the previous layer based on a live input signal (light sensor, to start),
constrained by a "max overhang" clamp so the wall never loses bond with the
layer below. Eventually feeding an ESP32-based light sensor (LTR390) into a
realtime control loop that streams gcode directly to an Ultimaker 2+ over USB
serial, bypassing normal slice-then-print workflow.

Part of Sui's PhD practice (ANU School of Cybernetics) — connects to the
"Birch as Cybernetic System" project and "yestalgia" framing (objects that
persist and respond to environment over time).

## Hardware in play
- **Raspberry Pi 5**, hostname `gen`, fresh Raspberry Pi OS (64-bit Lite),
  headless, SSH enabled. Currently on a phone hotspot (ANU wifi is
  WPA2-Enterprise and the Pi Imager can't configure that directly — sort out
  proper network config later if needed).
- **Ultimaker 2+**, connected via USB to the Pi. Stock Marlin firmware
  (open, not vendor-locked like the S3). Just upgraded to a **0.8mm nozzle**
  to make thicker single-wall prints more structurally viable.
- ESP32 SuperMini boards available, already have LTR390 ambient light sensor
  integration code from prior "environments" project work — reuse rather
  than rebuild.
- Deliberately **not** using ESP-NOW for this — that's for mesh/multi-node
  setups (Birch project). Single sensor to single Pi doesn't need it; plain
  wired I2C/serial or simple wifi (MQTT/HTTP) is enough.

## Software state on the Pi (as of this handoff)
- Fresh OS, updated via `apt update && full-upgrade`.
- Working directory: `~/gen3d`
- Python venv at `~/gen3d/venv` with `pyserial` installed.
- Helper script `~/start_gen3d.sh` (run with `source ~/start_gen3d.sh`) cds
  into `~/gen3d` and activates the venv.
- **Not yet confirmed**: whether the venv activation actually completed
  successfully (`(venv)` prompt prefix) — check this first.
- **Not yet done**: finding the Ultimaker's serial device
  (`ls /dev/ttyACM* /dev/ttyUSB*`), confirming serial handshake at baud
  **250000** (Marlin default for UM2+, not the usual 115200).

## Immediate next steps
1. Confirm venv works: `cd ~/gen3d && source venv/bin/activate`, check
   prompt shows `(venv)`.
2. Find the printer's port: `ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null`
3. Sanity-check serial comms with a harmless M105 (temperature query) at
   250000 baud, confirm an `ok T:.. /.. B:.. /..` response.
4. Only after that's solid: build out the realtime gcode loop (per-layer
   path generation with a "max overhang" clamp expressed as % of extrusion
   line width, currently planned default ~40% overhang / 60% bond
   remaining). Do NOT trust early test code with real filament loaded —
   first runs should be movement-only / cold nozzle until extrusion (`E`
   value) math and proper start/end gcode are verified.

## Design decisions already settled (don't re-litigate these)
- **Max overhang clamp**, not "seal limit" — earlier version of this concept
  was mislabeled. The clamp is: `max_shift_mm = overhang_pct * line_width`.
  Remaining bonded overlap = `line_width - max_shift`. Sui is also using this
  clamp informally to cap overall wall slope change, which is doing useful
  work beyond just the bonding concern — not flagged as a problem so far.
- **No Homeostat / return-to-circle-X for now** — parked, may revisit later
  if the printed object should feel "anchored" rather than free-drifting.
- **Disturbance model**: input arrives as a single fresh sample at a fixed
  real-world interval (e.g. every 0.5s) — NOT a spatial noise field, NOT
  per-point independent jitter (caused ugly spiky/star-shaped artifacts in
  early visualiser versions), NOT sine-harmonic "lobes" (felt artificial/too
  designed). The clamp itself is what's currently doing the "diffusion" and
  "smoothing" work — separate diffusion/injection-rate/persistence params
  were deemed redundant once sampling is tied to a real clock.
- **Sensor choice**: ambient light (reusing LTR390 work) was chosen over a
  mic, specifically because daylight/cloud variation operates on a timescale
  that matches the project's "persistence" theme, rather than fast spiky
  noise that's easier to demo but thematically empty. Heart rate sensor
  flagged as a strong *future* direction (exhibition-relevant: "watch your
  vessel being printed off your own heartbeat") but parked until the basic
  pipeline is proven.
- **OctoPrint**: deliberately NOT using it for the realtime loop. Direct
  `pyserial` control of the USB port is simpler and sufficient. OctoPrint
  could wrap this later for web UI/monitoring, but isn't a prerequisite.
- **Pi 3 vs Pi 5**: Pi 3 would have been plenty (this is I/O-bound on the
  printer's serial handshake, not compute-bound) — Pi 5 was just what was on
  hand, no functional reason it needs to be a 5.

## Working style notes
- Sui prefers things confirmed working in small steps rather than long
  speculative code dumps — go one verified step at a time.
- Direct, concise, no over-explaining. Sarcasm-sincerity balance is fine.
- Push back/flag risk plainly rather than just agreeing (e.g. already
  flagged: random-walk drift with no return force will eventually wander
  arbitrarily far from the starting shape; real bond strength is probably
  non-linear with overlap, not linear, so the overhang clamp ceiling should
  ideally be based on real Ultimaker test results, not guessed).
