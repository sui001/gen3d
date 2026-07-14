VERSION = "1.7"
import math, time, random


class Gen3DPath:

    def __init__(self, sides=0, diameter=50.0, n_points=72,
                 line_width=0.8, max_overhang_pct=0.40,
                 print_speed=20.0, sample_interval=0.0,
                 wobble_amp=0.0, wobble_freq=3, phase_drift=0.15,
                 point_smooth_mm=2.0,
                 pitch_centre_hz=500.0, pitch_range_hz=400.0,
                 sensor_read_fn=None, sensor_to_target_fn=None):

        self.sides           = sides
        self.base_radius     = diameter / 2
        self.n_points        = n_points
        self.line_width      = line_width
        self.max_overhang    = max_overhang_pct * line_width
        self.sample_interval = sample_interval
        self.wobble_amp      = wobble_amp
        self.wobble_freq     = wobble_freq
        self.phase_drift     = phase_drift
        self.point_smooth_mm = point_smooth_mm
        self.pitch_centre_hz = pitch_centre_hz
        self.pitch_range_hz  = pitch_range_hz

        self.sensor_read_fn      = sensor_read_fn      or (lambda: 0.0)
        self.sensor_to_target_fn = sensor_to_target_fn or (lambda x: x)

        self.prev_layer_r = [self.base_radius] * n_points

        self._target_offset = 0.0
        self._last_sample   = 0.0
        self._phase         = 0.0
        self._last_point_r  = None
        self.samples_this_layer = 0

    def _maybe_sample(self):
        now = time.time()
        if now - self._last_sample >= self.sample_interval:
            raw = self.sensor_read_fn()
            self._target_offset = self.sensor_to_target_fn(raw)
            self._last_sample   = now
            self.samples_this_layer += 1

    def _point_angle(self, i):
        if self.sides >= 3:
            angle  = 2 * math.pi * i / self.n_points
            sector = math.floor(angle / (2 * math.pi / self.sides))
            return 2 * math.pi * sector / self.sides
        return 2 * math.pi * i / self.n_points

    def step_point(self, point_idx):
        self._maybe_sample()

        angle  = self._point_angle(point_idx)
        prev_r = self.prev_layer_r[point_idx]

        # pitch bias: sustained freq deviation pushes wall in or out
        # high pitch → outward, low pitch → inward, centre → neutral
        try:
            import main as _m
            freq = _m._current_freq_hz[0]
        except Exception:
            freq = self.pitch_centre_hz
        bias = (freq - self.pitch_centre_hz) / max(1.0, self.pitch_range_hz)
        bias = max(-1.0, min(1.0, bias))

        # amplitude drives chaos, pitch drives direction
        chaos     = (random.random() - 0.5) * 2.0 * self._target_offset
        noise     = chaos + bias * self._target_offset
        desired_r = prev_r + noise

        # optional sinusoidal wobble on top
        if self.wobble_amp > 0:
            desired_r += self.wobble_amp * math.sin(self.wobble_freq * angle + self._phase)

        # vertical clamp: can't overhang too far from previous layer
        clamped_r = max(prev_r - self.max_overhang,
                        min(prev_r + self.max_overhang, desired_r))
        clamped_r = max(1.0, clamped_r)

        # horizontal clamp: stop adjacent points diverging too far
        if self._last_point_r is not None:
            clamped_r = max(self._last_point_r - self.point_smooth_mm,
                            min(self._last_point_r + self.point_smooth_mm, clamped_r))
            clamped_r = max(1.0, clamped_r)
        self._last_point_r = clamped_r

        x = clamped_r * math.cos(angle)
        y = clamped_r * math.sin(angle)
        return x, y, clamped_r

    def finish_layer(self, layer_radii):
        self.prev_layer_r       = list(layer_radii)
        self.samples_this_layer = 0
        self._phase            += self.phase_drift
        self._last_point_r      = None

    def set_n(self, n_new):
        if n_new == self.n_points:
            return
        old_n = self.n_points
        old_r = self.prev_layer_r
        new_r = []
        for i in range(n_new):
            frac  = i / n_new
            old_f = frac * old_n
            lo = int(old_f) % old_n
            hi = (lo + 1) % old_n
            t  = old_f - int(old_f)
            new_r.append(old_r[lo] * (1 - t) + old_r[hi] * t)
        self.prev_layer_r = new_r
        self.n_points = n_new
