import math, time, random


class Gen3DPath:
    """
    Per-point radius variation driven by a live sensor.

    Each call to step_point(i) returns the (x, y, r) for point i on the
    current layer. The radius for each point is the previous layer radius
    at that point, plus a clamped offset derived from the sensor reading.

    finish_layer(layer_radii) must be called after all points are stepped
    to store the layer for the next layer reference.
    """

    def __init__(self, sides=0, diameter=50.0, n_points=72,
                 line_width=0.8, max_overhang_pct=0.40,
                 print_speed=20.0, sample_interval=0.5,
                 sensor_read_fn=None, sensor_to_target_fn=None):

        self.sides            = sides
        self.base_radius      = diameter / 2
        self.n_points         = n_points
        self.line_width       = line_width
        self.max_overhang     = max_overhang_pct * line_width
        self.sample_interval  = sample_interval

        self.sensor_read_fn      = sensor_read_fn      or (lambda: 0.0)
        self.sensor_to_target_fn = sensor_to_target_fn or (lambda x: x)

        # per-point radius of previous layer -- starts as perfect circle
        self.prev_layer_r = [self.base_radius] * n_points

        # current sensor target offset (mm)
        self._target_offset = 0.0
        self._last_sample   = 0.0
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
            seg    = self.sides
            angle  = 2 * math.pi * i / self.n_points
            sector = math.floor(angle / (2 * math.pi / seg))
            return  2 * math.pi * sector / seg
        return 2 * math.pi * i / self.n_points

    def step_point(self, point_idx):
        """Return (x, y, r) for point_idx on the current layer."""
        self._maybe_sample()

        prev_r    = self.prev_layer_r[point_idx]
        desired_r = prev_r + self._target_offset

        # clamp to max overhang from previous layer position
        clamped_r = max(prev_r - self.max_overhang,
                        min(prev_r + self.max_overhang, desired_r))
        clamped_r = max(1.0, clamped_r)

        angle = self._point_angle(point_idx)
        x = clamped_r * math.cos(angle)
        y = clamped_r * math.sin(angle)

        return x, y, clamped_r

    def finish_layer(self, layer_radii):
        """Call after all points in a layer are stepped."""
        self.prev_layer_r       = list(layer_radii)
        self.samples_this_layer = 0
