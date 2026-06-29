import argparse, math, time, random, threading
from gen3d_path import Gen3DPath

FILAMENT_DIAMETER = 2.85
PORT              = '/dev/ttyACM0'
SOUND_PORT        = '/dev/ttyACM1'
BED_CX, BED_CY    = 100.0, 100.0
SAFE_Z            = 10.0
FEEDRATE_TRAVEL   = 3000
PRIME_X, PRIME_Y  = 5.0, 20.0

DEFAULTS = dict(
    nozzle_dia    = 1.5,
    line_width    = 2.0,
    layer_height  = 0.7,
    diameter      = 50.0,
    total_layers  = 40,
    base_layers   = 1,
    max_overhang  = 0.40,
    nozzle_temp   = 210,
    bed_temp      = 60,
    print_speed   = 15.0,
    flow_pct      = 100,
    sides         = 0,
    n_points      = 72,
    sensor_source = 'sound',
    sensor_centre = 48.0,
    sensor_range  = 10.0,
    sensor_amp    = 5.0,
)


def feedrate(mm_s):
    return int(mm_s * 60)


def e_per_mm(line_width, layer_height):
    area = math.pi * (FILAMENT_DIAMETER / 2) ** 2
    return (line_width * layer_height) / area


def read_cpu_temp():
    with open('/sys/class/thermal/thermal_zone0/temp') as f:
        return int(f.read().strip()) / 1000.0


def make_temp_to_offset(centre, range_c, amp_mm):
    half = range_c / 2
    def fn(raw):
        return (max(centre - half, min(centre + half, raw)) - centre) / half * amp_mm
    return fn


def make_sound_sensor(port=SOUND_PORT, baud=115200, amp_mm=5.0):
    import serial
    s = serial.Serial(port, baud, timeout=0.5)
    s.dtr = False
    s.rts = False
    last = [0.0]
    running = [True]

    def _reader():
        while running[0]:
            try:
                line = s.readline().decode(errors='replace').strip()
                if line:
                    last[0] = float(line) * amp_mm
            except:
                pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    def read_fn():
        return last[0]

    def close_fn():
        running[0] = False
        s.close()

    return read_fn, close_fn


def perimeter_gcode(radius, line_width, layer_height, e, feedrate_print,
                    seam_offset=0, n_pts=72, on_point=None):
    epm = e_per_mm(line_width, layer_height)
    lines = []
    pts = [
        (BED_CX + radius * math.cos(2 * math.pi * i / n_pts),
         BED_CY + radius * math.sin(2 * math.pi * i / n_pts))
        for i in range(n_pts)
    ]
    pts = pts[seam_offset:] + pts[:seam_offset]
    pts_closed = pts + [pts[0]]
    lines.append('G0 X{:.3f} Y{:.3f} F{}'.format(pts[0][0], pts[0][1], FEEDRATE_TRAVEL))
    for (x1, y1), (x2, y2) in zip(pts_closed, pts_closed[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        e += seg * epm
        lines.append('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, feedrate_print))
        if on_point:
            on_point(x2, y2)
    return lines, e


def base_layer_gcode(radius, line_width, layer_height, e, layer_num,
                     feedrate_print, on_point=None):
    lines = []
    epm = e_per_mm(line_width, layer_height)
    perim_lines, e = perimeter_gcode(
        radius, line_width, layer_height, e, feedrate_print,
        n_pts=72, on_point=on_point)
    lines.extend(perim_lines)
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
        seg = math.hypot(x2 - x1, y2 - y1)
        e += seg * epm
        lines.append('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, feedrate_print))
        if on_point:
            on_point(x2, y2)
        pos += spacing; row += 1
    return lines, e


def run_print(p=None, dry_run=False, on_layer=None, on_point=None,
              sensor_fn=None, stop_flag=None):
    if p is None:
        p = DEFAULTS.copy()

    sound_close = None

    if sensor_fn is None:
        if p.get('sensor_source', 'cpu') == 'sound':
            sensor_fn, sound_close = make_sound_sensor(amp_mm=p.get('sensor_amp', 5.0))
            sensor_to_offset = lambda x: x
        else:
            sensor_fn = read_cpu_temp
            sensor_to_offset = make_temp_to_offset(
                p.get('sensor_centre', 48.0),
                p.get('sensor_range',  10.0),
                p.get('sensor_amp',     5.0),
            )
    else:
        sensor_to_offset = make_temp_to_offset(
            p.get('sensor_centre', 48.0),
            p.get('sensor_range',  10.0),
            p.get('sensor_amp',     5.0),
        )

    fr_print = feedrate(p['print_speed'])
    epm = e_per_mm(p['line_width'], p['layer_height'])
    radius = p['diameter'] / 2

    printer = None
    if not dry_run:
        from printer import Printer
        printer = Printer(PORT)
        try:
            import viz_app
            viz_app._printer_ref[0] = printer
        except ImportError:
            pass

    def send(cmd):
        if not dry_run:
            printer.send(cmd)

    for cmd in ['G21', 'G90', 'M82',
                'M190 S{}'.format(p["bed_temp"]),
                'M109 S{}'.format(p["nozzle_temp"]),
                'G28',
                'G0 Z{} F{}'.format(SAFE_Z, FEEDRATE_TRAVEL),
                'G92 E0',
                'M221 S{}'.format(p.get("flow_pct", 100)),
                'M155 S2']:
        send(cmd)

    e = 0.0
    prime_epm = e_per_mm(p['line_width'], p['layer_height'])
    for cmd in [
        'G0 X{} Y{} F{}'.format(PRIME_X, PRIME_Y, FEEDRATE_TRAVEL),
        'G0 Z{:.3f} F{}'.format(p['layer_height'], FEEDRATE_TRAVEL),
        'G1 X{} Y{} E{:.5f} F400'.format(PRIME_X, PRIME_Y + 40, 40 * prime_epm),
        'G1 E{:.5f} F3000'.format(40 * prime_epm - 1.0),
        'G0 Z{} F{}'.format(SAFE_Z, FEEDRATE_TRAVEL),
    ]:
        send(cmd)
    e = 0.0
    send('G92 E0')

    path = Gen3DPath(
        sides=p['sides'], diameter=p['diameter'], n_points=p['n_points'],
        line_width=p['line_width'], max_overhang_pct=p['max_overhang'],
        print_speed=p['print_speed'], sample_interval=0.5,
        sensor_read_fn=sensor_fn,
        sensor_to_target_fn=sensor_to_offset,
    )

    RETRACT_MM = 4.0
    layer_times = []
    prev_mean_r = radius

    for layer in range(1, p['total_layers'] + 1):
        if stop_flag and stop_flag.is_set():
            print('Stop requested -- parking.')
            break

        t_start = time.time()
        z = layer * p['layer_height']
        is_base = layer <= p['base_layers']

        if is_base:
            send('G1 E{:.5f} F3000'.format(e - RETRACT_MM))
            send('G0 X{} Y{} F{}'.format(BED_CX, BED_CY, FEEDRATE_TRAVEL))
            send('G0 Z{:.3f} F{}'.format(z, FEEDRATE_TRAVEL))
            send('G1 E{:.5f} F{}'.format(e, fr_print))
            lines, e = base_layer_gcode(
                radius, p['line_width'], p['layer_height'], e, layer,
                fr_print, on_point=on_point)
            for cmd in lines:
                send(cmd)
            pts = []; samples = 0; mean_r = radius
        else:
            layer_radii, pts = [], []
            for pt in range(p['n_points']):
                x, y, r = path.step_point(pt)
                layer_radii.append(r)
                pts.append((BED_CX + x, BED_CY + y))
            samples = path.samples_this_layer
            path.finish_layer(layer_radii)
            mean_r = sum(layer_radii) / len(layer_radii)
            pts_closed = pts + [pts[0]]
            send('G1 E{:.5f} F3000'.format(e - RETRACT_MM))
            send('G0 X{:.3f} Y{:.3f} F{}'.format(pts[0][0], pts[0][1], FEEDRATE_TRAVEL))
            send('G0 Z{:.3f} F{}'.format(z, FEEDRATE_TRAVEL))
            send('G1 E{:.5f} F{}'.format(e, fr_print))
            for (x1, y1), (x2, y2) in zip(pts_closed, pts_closed[1:]):
                seg = math.hypot(x2 - x1, y2 - y1)
                e += seg * epm
                send('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, fr_print))
                if on_point:
                    on_point(x2, y2)

        layer_times.append(time.time() - t_start)
        avg_t = sum(layer_times[-5:]) / len(layer_times[-5:])
        remaining_s = avg_t * (p['total_layers'] - layer)
        sensor_raw = sensor_fn()
        radius_delta = mean_r - prev_mean_r
        prev_mean_r = mean_r

        if on_layer:
            on_layer(layer, pts, z, samples, sensor_raw, is_base, radius_delta, remaining_s)
        else:
            d = 'up' if radius_delta > 0.01 else ('dn' if radius_delta < -0.01 else '--')
            eta = time.strftime('%M:%S', time.gmtime(remaining_s))
            src = p.get('sensor_source', 'cpu')
            print('Layer {:3d} {}  z={:.2f}mm  {}={:.3f} {}  eta={}'.format(
                layer, '[base]' if is_base else '[wall]', z, src, sensor_raw, d, eta))

    for cmd in ['G91', 'G1 E-6 F3000',
                'G0 Z10 F{}'.format(FEEDRATE_TRAVEL),
                'G90', 'G0 X10 Y200 F{}'.format(FEEDRATE_TRAVEL),
                'M104 S0', 'M140 S0', 'M84']:
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
    ap.add_argument('--sensor-source', type=str,   default=DEFAULTS['sensor_source'])
    args = ap.parse_args()

    p = DEFAULTS.copy()
    p['total_layers']  = args.layers
    p['base_layers']   = args.base_layers
    p['layer_height']  = args.layer_height
    p['diameter']      = args.diameter
    p['line_width']    = args.line_width
    p['sides']         = args.sides
    p['sensor_source'] = args.sensor_source
    run_print(p=p, dry_run=args.dry_run)
