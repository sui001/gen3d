VERSION = "2.0-ceramic"
# ---------------------------------------------------------------------------
# gen3d main -- CERAMIC + sound-reactive print engine (CREEP / groundskeeper)
#
# This machine drives a DM556T-fed ceramic extruder (93mm barrel -> 10mm PTFE
# hose -> nozzle) via the Ender's own Marlin over the E0 logic tap. It runs
# COLD -- there is no hotend heater -- so we allow cold extrusion and never
# wait on temperatures. Rod motion is tiny because of the barrel:line area
# ratio; flow is a pressure spring, so we NEVER retract (that bleeds the
# charge) and never move E faster than the print feedrate.
#
# Sound (INMP441 I2S mic on the ESP32-S3) streams "freq_hz,amplitude" CSV:
#   amplitude -> radius chaos (loud = more wobble) + point density
#   freq_hz   -> pitch bias direction (high = wall drifts out, low = in)
#
# Point count is DISTANCE-based: each layer, n_points = circumference / spacing
# (spacing tightens with amplitude), so the object varies as it grows -- not a
# fixed 72.  <- the feature you asked about; lives here now.
#
# CERAMIC SAFETY INVARIANTS (do not "optimise" away -- each cost a debug day):
#   * NO heating commands (M190/M109/M104/M140 for print temps). Cold by design.
#   * M302 P1 every run to allow cold extrusion (not persisted in EEPROM).
#   * NO retraction. RETRACT_MM=0. Retracts decompress the pressure spring.
#   * NO E move faster than the XY print feedrate; prime blob at F6 (<=F12).
#     F30+ E moves crash this Marlin build dead -> needs a power cycle.
#   * M999 after connect -- BLTouch throws a boot error that silently drops G1s.
#   * End: lift straight up only. NEVER park to a bed corner -- hose can't reach.
# ---------------------------------------------------------------------------
import argparse, math, time, random, threading
from gen3d_path import Gen3DPath

# 93mm barrel ID -> e_per_mm() area == barrel area (6792.9 mm^2). E gcode = rod mm.
FILAMENT_DIAMETER = 93.0
PORT              = '/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10JKQMX-if00-port0'
SOUND_PORT        = '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_AC:27:6E:CC:E7:B8-if00'
BED_CX, BED_CY    = 180.0, 180.0   # 360x360 bed centre
SAFE_Z            = 10.0
FEEDRATE_TRAVEL   = 1000            # ceramic: gentle travels (hose + gantry)
PRIME_X, PRIME_Y  = 150.0, 180.0   # off to the side, clear of the circle, hose-reachable
PRIME_ROD         = 0.25           # rod mm for the prime blob (real charge comes from jog pre-prime)
BOOT_WAIT         = 12.0           # Mega DTR-reset + BLTouch self-test settle

_current_freq_hz    = [0.0]
_current_voice_mm   = [0.0]
_current_voice_x    = [0.0]   # unused with 2-field mic; kept for viz_app /voice compat
_current_voice_y    = [0.0]   # unused with 2-field mic; kept for viz_app /voice compat
_current_n_points   = [0]
_current_spacing_mm = [0.0]

DEFAULTS = dict(
    nozzle_dia    = 2.5,
    line_width    = 2.5,
    layer_height  = 1.0,
    diameter      = 40.0,
    total_layers  = 10,
    base_layers   = 0,        # single-wall; no raster infill for ceramic
    max_overhang  = 0.40,
    nozzle_temp   = 0,        # COLD -- unused, kept for viz_app compatibility
    bed_temp      = 0,        # COLD -- unused
    print_speed   = 6.0,      # mm/s XY == F360 (proven ceramic speed)
    flow_pct      = 120,      # M221 -- print_test FLOW 1.2
    sides         = 0,
    n_points      = 72,       # fallback only; distance-based n_next overrides per layer
    sensor_source = 'sound',
    sensor_centre = 48.0,
    sensor_range  = 10.0,
    sensor_amp    = 30.0,     # amplitude -> radius offset gain (tune live in UI)
    pitch_centre_hz = 400.0,
    pitch_range_hz  = 300.0,
    # distance-based point spacing (mm of circumference per point)
    spacing_min_mm = 1.5,     # loudest -> densest points
    spacing_max_mm = 3.0,     # quietest -> sparsest points
    spacing_sens   = 0.6,     # sensor value that reaches max density
)


def feedrate(mm_s):
    return int(mm_s * 60)


def e_per_mm(line_width, layer_height):
    area = math.pi * (FILAMENT_DIAMETER / 2) ** 2
    return (line_width * layer_height) / area


def make_sound_sensor(port=SOUND_PORT, baud=115200, amp_mm=5.0):
    """Reads the INMP441 'freq_hz,amplitude' CSV stream.
    Returns (read_fn, close_fn, reset_fn). read_fn() -> amplitude*amp_mm."""
    import serial
    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.5
    s.dtr = False
    s.rts = False
    s.open()
    last = [0.0]
    running = [True]

    def _reader():
        while running[0]:
            try:
                line = s.readline().decode(errors='replace').strip()
                if not line:
                    continue
                if ',' in line:
                    parts = line.split(',')
                    _current_freq_hz[0] = float(parts[0])
                    last[0] = float(parts[1]) * amp_mm
                else:
                    last[0] = float(line) * amp_mm
                _current_voice_mm[0] = last[0]
            except serial.SerialException:
                time.sleep(0.2)
            except Exception:
                pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    def read_fn():
        return last[0]

    def close_fn():
        running[0] = False
        s.close()

    def reset_fn():
        # seed a fresh baseline right as printing motion resumes (not mid-travel)
        try:
            s.reset_input_buffer()
        except Exception:
            pass

    return read_fn, close_fn, reset_fn


def base_layer_gcode(radius, line_width, layer_height, e, layer_num,
                     feedrate_print, on_point=None):
    """Solid raster disc (only used if base_layers > 0). Ceramic-safe: all E
    coupled to XY at feedrate_print, never a bare fast E move."""
    lines = []
    epm = e_per_mm(line_width, layer_height)
    # perimeter
    pts = [(BED_CX + radius * math.cos(2 * math.pi * i / 72),
            BED_CY + radius * math.sin(2 * math.pi * i / 72)) for i in range(72)]
    pts_closed = pts + [pts[0]]
    lines.append('G0 X{:.3f} Y{:.3f} F{}'.format(pts[0][0], pts[0][1], FEEDRATE_TRAVEL))
    for (x1, y1), (x2, y2) in zip(pts_closed, pts_closed[1:]):
        e += math.hypot(x2 - x1, y2 - y1) * epm
        lines.append('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, feedrate_print))
        if on_point:
            on_point(x2, y2)
    # infill
    infill_r = radius - line_width
    spacing = line_width * 0.95
    horizontal = (layer_num % 2 == 0)
    pos = -infill_r + spacing / 2
    row = 0
    while pos <= infill_r:
        half = math.sqrt(max(0, infill_r**2 - pos**2))
        if half < line_width / 2:
            pos += spacing; row += 1; continue
        if horizontal:
            x1, y1 = BED_CX - half, BED_CY + pos
            x2, y2 = BED_CX + half, BED_CY + pos
        else:
            x1, y1 = BED_CX + pos, BED_CY - half
            x2, y2 = BED_CX + pos, BED_CY + half
        if row % 2 == 1:
            x1, y1, x2, y2 = x2, y2, x1, y1
        lines.append('G0 X{:.3f} Y{:.3f} F{}'.format(x1, y1, FEEDRATE_TRAVEL))
        e += math.hypot(x2 - x1, y2 - y1) * epm
        lines.append('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, feedrate_print))
        if on_point:
            on_point(x2, y2)
        pos += spacing; row += 1
    return lines, e


def run_print(p=None, dry_run=False, on_layer=None, on_point=None,
              sensor_fn=None, stop_flag=None):
    if p is None:
        p = DEFAULTS.copy()

    sound_close = sound_reset = None
    if sensor_fn is None:
        sensor_fn, sound_close, sound_reset = make_sound_sensor(amp_mm=p.get('sensor_amp', 5.0))
    sensor_to_offset = lambda x: x   # amplitude is already an mm offset

    fr_print = feedrate(p['print_speed'])
    epm = e_per_mm(p['line_width'], p['layer_height'])
    radius = p['diameter'] / 2

    printer = None
    if not dry_run:
        from printer import Printer
        printer = Printer(PORT, boot_wait=BOOT_WAIT)
        try:
            import viz_app
            viz_app._printer_ref[0] = printer
        except ImportError:
            pass

    def send(cmd):
        if not dry_run:
            printer.send(cmd)
        else:
            print(cmd)

    # ---- ceramic preamble: cold, cold-extrude allowed, clear BLTouch state ----
    for cmd in ['M999',            # clear any BLTouch stopped-state (silently drops G1s otherwise)
                'T0',
                'M302 P1',         # ALLOW COLD EXTRUSION -- no heater on this head
                'G21', 'G90', 'M82',   # mm, absolute XYZ, absolute E
                'M221 S{}'.format(p.get('flow_pct', 100)),
                'M155 S2',
                'G28',
                'G0 Z{} F{}'.format(SAFE_Z, FEEDRATE_TRAVEL),
                'G92 E0',
                'M117 gen3d ceramic']:
        send(cmd)

    # ---- prime blob (top-up; the real charge is the jog.py pre-prime) ----
    e = 0.0
    for cmd in [
        'G0 X{} Y{} F{}'.format(PRIME_X, PRIME_Y, FEEDRATE_TRAVEL),
        'G0 Z{:.3f} F{}'.format(p['layer_height'], FEEDRATE_TRAVEL),
        'G1 E{:.5f} F6'.format(PRIME_ROD),   # stationary charge, slow (<=F12)
        'G0 Z{} F{}'.format(SAFE_Z, FEEDRATE_TRAVEL),
    ]:
        send(cmd)
    e = 0.0
    send('G92 E0')

    path = Gen3DPath(
        sides=p['sides'], diameter=p['diameter'], n_points=p['n_points'],
        line_width=p['line_width'], max_overhang_pct=p['max_overhang'],
        print_speed=p['print_speed'], sample_interval=0.0,
        wobble_amp=p.get('wobble_amp', 0.0),
        wobble_freq=int(p.get('wobble_freq', 3)),
        phase_drift=p.get('phase_drift', 0.15),
        point_smooth_mm=p.get('point_smooth_mm', 2.0),
        pitch_centre_hz=p.get('pitch_centre_hz', 400.0),
        pitch_range_hz=p.get('pitch_range_hz', 300.0),
        sensor_read_fn=sensor_fn,
        sensor_to_target_fn=sensor_to_offset,
    )

    RETRACT_MM = 0.0   # CERAMIC: never retract (pressure spring)
    layer_times = []
    prev_mean_r = radius
    n_next = p['n_points']

    for layer in range(1, p['total_layers'] + 1):
        if stop_flag and stop_flag.is_set():
            print('Stop requested -- parking.')
            break

        t_start = time.time()
        z = layer * p['layer_height']
        is_base = layer <= p['base_layers']

        if is_base:
            send('G0 X{} Y{} F{}'.format(BED_CX, BED_CY, FEEDRATE_TRAVEL))
            send('G0 Z{:.3f} F{}'.format(z, FEEDRATE_TRAVEL))
            lines, e = base_layer_gcode(
                radius, p['line_width'], p['layer_height'], e, layer,
                fr_print, on_point=on_point)
            for cmd in lines:
                send(cmd)
            pts = []; samples = 0; mean_r = radius
        else:
            n = n_next
            path.set_n(n)

            # point 0 first -- it's the travel target before printing resumes
            x0, y0, r0 = path.step_point(0)
            pt0 = (BED_CX + x0, BED_CY + y0)
            layer_radii = [r0]
            pts = [pt0]

            send('G0 X{:.3f} Y{:.3f} F{}'.format(pt0[0], pt0[1], FEEDRATE_TRAVEL))
            send('G0 Z{:.3f} F{}'.format(z, FEEDRATE_TRAVEL))
            if sound_reset:
                sound_reset()   # fresh sound baseline now that motion resumes
            if on_point:
                on_point(pt0[0], pt0[1])
            prev_xy = pt0

            # sample each point's radius right before printing it (real-time sound)
            for i in range(1, n):
                x, y, r = path.step_point(i)
                layer_radii.append(r)
                x2, y2 = BED_CX + x, BED_CY + y
                pts.append((x2, y2))
                seg = math.hypot(x2 - prev_xy[0], y2 - prev_xy[1])
                e += seg * epm
                send('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, fr_print))
                if on_point:
                    on_point(x2, y2)
                prev_xy = (x2, y2)
                # pace the host to real motion so sampling tracks the sound
                expected_s = seg / max(0.1, p['print_speed'])
                time.sleep(min(expected_s, 1.0))

            # close back to point 0
            e += math.hypot(pt0[0] - prev_xy[0], pt0[1] - prev_xy[1]) * epm
            send('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(pt0[0], pt0[1], e, fr_print))
            if on_point:
                on_point(pt0[0], pt0[1])

            samples = path.samples_this_layer
            path.finish_layer(layer_radii)
            mean_r = sum(layer_radii) / len(layer_radii)

        layer_times.append(time.time() - t_start)
        avg_t = sum(layer_times[-5:]) / len(layer_times[-5:])
        remaining_s = avg_t * (p['total_layers'] - layer)
        sensor_raw = sensor_fn()
        radius_delta = mean_r - prev_mean_r
        prev_mean_r = mean_r

        # distance-based point count for NEXT layer: circumference / spacing,
        # spacing tightening with amplitude (loud -> denser -> more detail)
        amp_now      = sensor_raw
        spacing_sens = p.get('spacing_sens', 0.6)
        spacing_min  = p.get('spacing_min_mm', 1.5)
        spacing_max  = p.get('spacing_max_mm', 3.0)
        t_amp        = min(1.0, amp_now / max(0.001, spacing_sens))
        spacing_mm   = spacing_max - t_amp * (spacing_max - spacing_min)
        n_next       = max(6, round(2 * math.pi * mean_r / spacing_mm))
        _current_n_points[0]   = n_next
        _current_spacing_mm[0] = round(spacing_mm, 2)

        if on_layer:
            on_layer(layer, pts, z, samples, sensor_raw, is_base, radius_delta, remaining_s)
        else:
            d = 'up' if radius_delta > 0.01 else ('dn' if radius_delta < -0.01 else '--')
            eta = time.strftime('%M:%S', time.gmtime(remaining_s))
            print('Layer {:3d} {}  z={:.2f}mm  amp={:.3f} {}  freq={:.0f}Hz  n={} sp={:.1f}  eta={}'.format(
                layer, '[base]' if is_base else '[wall]', z, sensor_raw, d,
                _current_freq_hz[0], n_next, spacing_mm, eta))

    # ---- end: lift straight up only. NEVER retract, NEVER park to a corner. ----
    for cmd in ['G91',
                'G1 Z10 F{}'.format(FEEDRATE_TRAVEL),   # relative up, no X/Y, no E
                'G90',
                'M84']:
        send(cmd)

    if printer:
        printer.close()
    if sound_close:
        sound_close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run',       action='store_true')
    ap.add_argument('--layers',        type=int,   default=DEFAULTS['total_layers'])
    ap.add_argument('--base-layers',   type=int,   default=DEFAULTS['base_layers'])
    ap.add_argument('--layer-height',  type=float, default=DEFAULTS['layer_height'])
    ap.add_argument('--diameter',      type=float, default=DEFAULTS['diameter'])
    ap.add_argument('--line-width',    type=float, default=DEFAULTS['line_width'])
    ap.add_argument('--sides',         type=int,   default=DEFAULTS['sides'])
    args = ap.parse_args()

    p = DEFAULTS.copy()
    p['total_layers']  = args.layers
    p['base_layers']   = args.base_layers
    p['layer_height']  = args.layer_height
    p['diameter']      = args.diameter
    p['line_width']    = args.line_width
    p['sides']         = args.sides
    run_print(p=p, dry_run=args.dry_run)
