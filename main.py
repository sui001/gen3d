import argparse, math, time, random
from gen3d_path import Gen3DPath

FILAMENT_DIAMETER = 2.85
PORT              = '/dev/ttyACM0'
BED_CX, BED_CY    = 100.0, 100.0
SAFE_Z            = 10.0
FEEDRATE_TRAVEL   = 3000
PRIME_X, PRIME_Y  = 5.0, 20.0   # prime line position, clear of clips

DEFAULTS = dict(
    nozzle_dia    = 0.8,
    line_width    = 0.8,
    layer_height  = 0.3,
    diameter      = 50.0,
    total_layers  = 40,
    base_layers   = 3,
    max_overhang  = 0.40,
    nozzle_temp   = 210,
    bed_temp      = 60,
    print_speed   = 20.0,
    flow_pct      = 100,
    sides         = 0,
    n_points      = 72,
    sensor_centre = 50.0,  # °C — temp that maps to zero offset
    sensor_range  = 40.0,  # °C — full ± range (smaller = more sensitive)
    sensor_amp    = 5.0,   # mm — max radius offset at edge of range
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
    """Returns a sensor_to_target_fn using current UI params."""
    half = range_c / 2
    def fn(raw):
        return (max(centre - half, min(centre + half, raw)) - centre) / half * amp_mm
    return fn


def perimeter_gcode(radius, line_width, layer_height, e, feedrate_print,
                    seam_offset=0, n_pts=72, on_point=None):
    """Single perimeter ring at given radius, starting at seam_offset point."""
    epm = e_per_mm(line_width, layer_height)
    lines = []
    pts = [
        (BED_CX + radius * math.cos(2 * math.pi * i / n_pts),
         BED_CY + radius * math.sin(2 * math.pi * i / n_pts))
        for i in range(n_pts)
    ]
    # rotate seam
    pts = pts[seam_offset:] + pts[:seam_offset]
    pts_closed = pts + [pts[0]]

    lines.append(f'G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f} F{FEEDRATE_TRAVEL}')
    for (x1, y1), (x2, y2) in zip(pts_closed, pts_closed[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        e += seg * epm
        lines.append(f'G1 X{x2:.3f} Y{y2:.3f} E{e:.5f} F{feedrate_print}')
        if on_point:
            on_point(x2, y2)
    return lines, e


def base_layer_gcode(radius, line_width, layer_height, e, layer_num,
                     feedrate_print, on_point=None):
    """Perimeter ring first, then crisscross infill inside."""
    lines = []
    epm = e_per_mm(line_width, layer_height)

    # perimeter first
    perim_lines, e = perimeter_gcode(
        radius, line_width, layer_height, e, feedrate_print,
        n_pts=72, on_point=on_point)
    lines.extend(perim_lines)

    # infill inside (inset by one line width so it stays inside the perimeter)
    infill_r = radius - line_width
    spacing = line_width * 0.95
    horizontal = (layer_num % 2 == 0)
    pos = -infill_r + spacing / 2
    row = 0
    while pos <= infill_r:
        half = math.sqrt(max(0, infill_r**2 - pos**2))
        if half < line_width / 2:
            pos += spacing
            row += 1
            continue
        if horizontal:
            x1, y1 = BED_CX - half, BED_CY + pos
            x2, y2 = BED_CX + half, BED_CY + pos
        else:
            x1, y1 = BED_CX + pos, BED_CY - half
            x2, y2 = BED_CX + pos, BED_CY + half
        if row % 2 == 1:
            x1, y1, x2, y2 = x2, y2, x1, y1
        lines.append(f'G0 X{x1:.3f} Y{y1:.3f} F{FEEDRATE_TRAVEL}')
        seg = math.hypot(x2 - x1, y2 - y1)
        e += seg * epm
        lines.append(f'G1 X{x2:.3f} Y{y2:.3f} E{e:.5f} F{feedrate_print}')
        if on_point:
            on_point(x2, y2)
        pos += spacing
        row += 1

    return lines, e


def run_print(p=None, dry_run=False, on_layer=None, on_point=None,
              sensor_fn=None, stop_flag=None):
    if p is None:
        p = DEFAULTS.copy()
    if sensor_fn is None:
        sensor_fn = read_cpu_temp

    fr_print = feedrate(p['print_speed'])
    epm = e_per_mm(p['line_width'], p['layer_height'])
    radius = p['diameter'] / 2

    printer = None
    if not dry_run:
        from printer import Printer
        printer = Printer(PORT)
        # expose to viz_app for live commands (set_flow etc)
        try:
            import viz_app
            viz_app._printer_ref[0] = printer
        except ImportError:
            pass

    def send(cmd):
        if not dry_run:
            printer.send(cmd)

    # --- start sequence ---
    for cmd in ['G21', 'G90', 'M82',
                f'M190 S{p["bed_temp"]}',
                f'M109 S{p["nozzle_temp"]}',
                'G28',
                f'G0 Z{SAFE_Z} F{FEEDRATE_TRAVEL}',
                'G92 E0',
                f'M221 S{p.get("flow_pct", 100)}',
                'M155 S2']:   # auto-report temps every 2s
        send(cmd)

    # --- prime line (front-left edge, clear of clips) ---
    e = 0.0
    prime_epm = e_per_mm(p['line_width'], p['layer_height'])
    for cmd in [
        f'G0 X{PRIME_X:.1f} Y{PRIME_Y:.1f} F{FEEDRATE_TRAVEL}',
        f'G0 Z{p["layer_height"]:.3f} F{FEEDRATE_TRAVEL}',
        # slow 40mm prime line
        f'G1 X{PRIME_X:.1f} Y{PRIME_Y + 40:.1f} E{40 * prime_epm:.5f} F400',
        # small retract before travel to print start
        f'G1 E{40 * prime_epm - 1.0:.5f} F3000',
        f'G0 Z{SAFE_Z} F{FEEDRATE_TRAVEL}',
    ]:
        send(cmd)
    e = 0.0
    send('G92 E0')   # reset E after prime

    # --- print layers ---
    temp_to_offset = make_temp_to_offset(
        p.get('sensor_centre', 50.0),
        p.get('sensor_range',  40.0),
        p.get('sensor_amp',     5.0),
    )
    path = Gen3DPath(
        sides=p['sides'], diameter=p['diameter'], n_points=p['n_points'],
        line_width=p['line_width'], max_overhang_pct=p['max_overhang'],
        print_speed=p['print_speed'], sample_interval=0.5,
        sensor_read_fn=sensor_fn,
        sensor_to_target_fn=temp_to_offset,
    )

    RETRACT_MM  = 4.0   # Bowden retract before travel
    PRIME_MM    = 3.8   # slightly less than retract to account for ooze

    layer_times = []
    prev_mean_r = radius

    for layer in range(1, p['total_layers'] + 1):
        if stop_flag and stop_flag.is_set():
            print('Stop requested — parking.')
            break

        t_start = time.time()
        z = layer * p['layer_height']
        is_base = layer <= p['base_layers']

        if is_base:
            send(f'G1 E{e - RETRACT_MM:.5f} F3000')   # retract
            send(f'G0 X{BED_CX:.3f} Y{BED_CY:.3f} F{FEEDRATE_TRAVEL}')
            send(f'G0 Z{z:.3f} F{FEEDRATE_TRAVEL}')
            send(f'G1 E{e:.5f} F{fr_print}')           # prime
            lines, e = base_layer_gcode(
                radius, p['line_width'], p['layer_height'], e, layer,
                fr_print, on_point=on_point)
            for cmd in lines:
                send(cmd)
            pts = []
            samples = 0
            mean_r = radius
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

            send(f'G1 E{e - RETRACT_MM:.5f} F3000')    # retract before travel
            send(f'G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f} F{FEEDRATE_TRAVEL}')
            send(f'G0 Z{z:.3f} F{FEEDRATE_TRAVEL}')
            send(f'G1 E{e:.5f} F{fr_print}')           # prime before extrusion
            for (x1, y1), (x2, y2) in zip(pts_closed, pts_closed[1:]):
                seg = math.hypot(x2 - x1, y2 - y1)
                e += seg * epm
                send(f'G1 X{x2:.3f} Y{y2:.3f} E{e:.5f} F{fr_print}')
                if on_point:
                    on_point(x2, y2)

        layer_times.append(time.time() - t_start)
        avg_t = sum(layer_times[-5:]) / len(layer_times[-5:])
        remaining_s = avg_t * (p['total_layers'] - layer)
        temp = sensor_fn()
        radius_delta = mean_r - prev_mean_r
        prev_mean_r = mean_r

        if on_layer:
            on_layer(layer, pts, z, samples, temp, is_base, radius_delta, remaining_s)
        else:
            d = '▲' if radius_delta > 0.01 else ('▼' if radius_delta < -0.01 else '─')
            eta = time.strftime('%M:%S', time.gmtime(remaining_s))
            print(f'Layer {layer:3d} {"[base]" if is_base else "[wall]"}'
                  f'  z={z:.2f}mm  cpu={temp:.1f}C {d}  eta={eta}')

    # --- end sequence: retract, lift, park, heaters off ---
    for cmd in [
        'G91',
        'G1 E-6 F3000',          # big retract to kill ooze streak
        f'G0 Z10 F{FEEDRATE_TRAVEL}',
        'G90',
        f'G0 X10 Y200 F{FEEDRATE_TRAVEL}',
        'M104 S0', 'M140 S0', 'M84',
    ]:
        send(cmd)

    if printer:
        printer.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run',      action='store_true')
    ap.add_argument('--layers',       type=int,   default=DEFAULTS['total_layers'])
    ap.add_argument('--base-layers',  type=int,   default=DEFAULTS['base_layers'])
    ap.add_argument('--layer-height', type=float, default=DEFAULTS['layer_height'])
    ap.add_argument('--diameter',     type=float, default=DEFAULTS['diameter'])
    ap.add_argument('--line-width',   type=float, default=DEFAULTS['line_width'])
    ap.add_argument('--sides',        type=int,   default=DEFAULTS['sides'])
    args = ap.parse_args()

    p = DEFAULTS.copy()
    p['total_layers'] = args.layers
    p['base_layers']  = args.base_layers
    p['layer_height'] = args.layer_height
    p['diameter']     = args.diameter
    p['line_width']   = args.line_width
    p['sides']        = args.sides
    run_print(p=p, dry_run=args.dry_run)
