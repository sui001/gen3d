"""
Robot locomotion abstraction.

Talks to an ESP32 over serial using a simple line protocol:
  MOVE 400.0   -- forward mm, waits for OK
  TURN 90.0    -- degrees clockwise (+) or CCW (-), waits for OK
  PARK         -- deploy cam feet, waits for OK
  UNPARK       -- retract feet, waits for OK
  SPRAY 2000   -- hold aerosol for N ms, waits for OK

In dry_run mode (no serial port given) all commands print to stdout.

Dead-reckoning position tracking via step counts. Accurate enough for
400mm range; no encoder needed with steppers.
"""

import math, time

class Mover:
    def __init__(self, port=None, baud=115200, wheel_radius_mm=30.0,
                 track_width_mm=200.0, spray_sweep_deg=20.0):
        self.wheel_r   = wheel_radius_mm
        self.track     = track_width_mm
        self.sweep_deg = spray_sweep_deg

        self.x       = 0.0   # mm, dead-reckoned
        self.y       = 0.0
        self.heading = 0.0   # degrees, 0 = +X axis, CCW positive

        self._dry = port is None
        self._s   = None

        if not self._dry:
            import serial
            self._s = serial.Serial(port, baud, timeout=30)
            time.sleep(1.5)
            self._drain()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def drive_to(self, tx, ty):
        """Turn to face (tx, ty) then drive straight there."""
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return

        target_heading = math.degrees(math.atan2(dy, dx))
        delta = _angle_diff(target_heading, self.heading)
        self._turn(delta)
        self._move(dist)

    def park(self):
        """Deploy cam feet. Robot is now locked to floor."""
        self._send("PARK")

    def unpark(self):
        """Retract feet. Robot is mobile again."""
        self._send("UNPARK")

    def spray(self, duration_ms=2000):
        """
        Spray hairspray then sweep for coverage.
        The sweep uses differential drive so the nozzle fans over the print area.
        """
        self._send(f"SPRAY {duration_ms}")
        # wiggle: left, centre, right, centre
        self._turn( self.sweep_deg / 2)
        self._turn(-self.sweep_deg)
        self._turn( self.sweep_deg / 2)

    def close(self):
        if self._s:
            self._s.close()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _move(self, mm):
        self._send(f"MOVE {mm:.1f}")
        rad = math.radians(self.heading)
        self.x += mm * math.cos(rad)
        self.y += mm * math.sin(rad)

    def _turn(self, degrees):
        if abs(degrees) < 0.1:
            return
        self._send(f"TURN {degrees:.1f}")
        self.heading = (self.heading + degrees) % 360

    def _send(self, cmd):
        if self._dry:
            print(f"[MOVER] {cmd}")
            return
        self._s.write((cmd.strip() + '\n').encode())
        while True:
            line = self._s.readline().decode(errors='replace').strip()
            if line == 'OK':
                return
            if line.startswith('ERR'):
                raise RuntimeError(f"Mover error: {line}")

    def _drain(self):
        while self._s.in_waiting:
            self._s.readline()


def _angle_diff(target, current):
    """Shortest signed angle from current to target heading (degrees)."""
    return (target - current + 180) % 360 - 180
