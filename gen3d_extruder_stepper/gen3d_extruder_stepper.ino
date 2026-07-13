// gen3d ceramic extruder axis controller
// ESP32-S3 SuperMini -> DM556T stepper driver (STEP/DIR/ENA opto inputs)
//
// Replaces the old Uno R3 Step_Motor_Driver.ino (pot speed + button dir/enable +
// wireless 3-way channel select, no gcode at all) with a single-axis controller
// that speaks the same "send line, block for ok" protocol gen3d already uses for
// the Ender 5 (see printer.py). Point a second Printer(port=...) at this board's
// serial port from gen3d and it drops straight in -- no host-side changes needed.
//
// Protocol (newline-terminated ASCII, one line per command):
//   G1 E<value> F<feedrate>   move E axis to absolute position, mm/min feedrate
//   G92 E<value>              set current E position without moving (retract accounting)
//   M17                       enable driver
//   M18 / M84                 disable driver
//   M92 E<value>              set + persist steps-per-mm; M92 alone reports current value
// Every line gets a single "ok" (or "Error: ...") response line, matching printer.py.
//
// DIP switches confirmed on this DM556T: SW1-4 ON -> 3200 pulses/rev, current set for
// peak 5.6A / RMS 4.0A. steps_per_mm below assumes a placeholder leadscrew pitch --
// calibrate for real with M92 (command a known distance, measure actual travel/output,
// recompute, then `M92 E<correct_value>` to persist it).

#include <AccelStepper.h>
#include <Preferences.h>

// ESP32-S3 SuperMini: avoid GPIO2 (board-specific strapping issue) and GPIO0/45/46
// (official strapping pins). These are plain safe GPIOs.
#define PIN_STEP 4
#define PIN_DIR  5
#define PIN_ENA  6

// Most Leadshine-style drivers enable on ENA- pulled LOW. Flip this if yours is opposite.
#define ENA_ACTIVE_LOW true

// DM556T is speced around 5V-logic opto inputs; the SuperMini's GPIOs are 3.3V.
// Verify PUL/DIR/ENA trigger reliably at 3.3V on your unit (scope or just test motion) --
// if not, add a 74HCT-series buffer or a small NPN switch between the ESP32 and the
// driver's "-" pins rather than relying on 3.3V alone.

#define STEPS_PER_REV 3200.0f          // confirmed: SW1-4 ON on this DM556T
#define LEADSCREW_PITCH_MM 8.0f        // TODO: replace with your plunger leadscrew's actual mm/rev
#define DEFAULT_STEPS_PER_MM (STEPS_PER_REV / LEADSCREW_PITCH_MM)

#define MAX_STEP_SPEED 20000.0f        // steps/sec ceiling AccelStepper will not exceed

Preferences prefs;
AccelStepper stepper(AccelStepper::DRIVER, PIN_STEP, PIN_DIR);

float stepsPerMm = DEFAULT_STEPS_PER_MM;
String lineBuf;

void applyStepsPerMm(float v) {
  stepsPerMm = v;
  prefs.putFloat("steps_per_mm", stepsPerMm);
}

void setup() {
  Serial.begin(115200);

  prefs.begin("extruder", false);
  stepsPerMm = prefs.getFloat("steps_per_mm", DEFAULT_STEPS_PER_MM);

  stepper.setEnablePin(PIN_ENA);
  stepper.setPinsInverted(false, false, ENA_ACTIVE_LOW); // dir, step, enable
  stepper.disableOutputs();

  stepper.setMaxSpeed(MAX_STEP_SPEED);
  stepper.setAcceleration(4000);        // steps/sec^2 -- tune to the mechanism
  stepper.setCurrentPosition(0);

  Serial.println("ok gen3d extruder ready");
}

void handleLine(String line) {
  line.trim();
  int semi = line.indexOf(';');
  if (semi >= 0) line = line.substring(0, semi);
  line.trim();
  if (line.length() == 0) { Serial.println("ok"); return; }

  if (line.startsWith("G1") || line.startsWith("G0")) {
    float eVal = NAN, fVal = NAN;
    int idx;
    if ((idx = line.indexOf('E')) >= 0) eVal = line.substring(idx + 1).toFloat();
    if ((idx = line.indexOf('F')) >= 0) fVal = line.substring(idx + 1).toFloat();

    if (!isnan(fVal) && fVal > 0) {
      float stepsPerSec = (fVal / 60.0f) * stepsPerMm;
      stepper.setMaxSpeed(min(stepsPerSec, MAX_STEP_SPEED));
    }
    if (!isnan(eVal)) {
      stepper.enableOutputs();
      long target = lround(eVal * stepsPerMm);
      stepper.moveTo(target);
      while (stepper.distanceToGo() != 0) stepper.run();
    }
    Serial.println("ok");
    return;
  }

  if (line.startsWith("G92")) {
    int idx = line.indexOf('E');
    float eVal = idx >= 0 ? line.substring(idx + 1).toFloat() : 0.0f;
    stepper.setCurrentPosition(lround(eVal * stepsPerMm));
    Serial.println("ok");
    return;
  }

  if (line.startsWith("M17")) { stepper.enableOutputs(); Serial.println("ok"); return; }
  if (line.startsWith("M18") || line.startsWith("M84")) { stepper.disableOutputs(); Serial.println("ok"); return; }

  if (line.startsWith("M92")) {
    int idx = line.indexOf('E');
    if (idx >= 0) applyStepsPerMm(line.substring(idx + 1).toFloat());
    Serial.print("ok steps_per_mm:");
    Serial.println(stepsPerMm, 3);
    return;
  }

  Serial.println("Error: unknown command");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleLine(lineBuf);
      lineBuf = "";
    } else if (c != '\r') {
      lineBuf += c;
    }
  }
}
