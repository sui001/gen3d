"""
Roaming printer mission controller.

Picks floor locations, drives to each, parks, sprays adhesive,
runs a print, then moves on. Leaves little blobs around the place.

Usage:
    python mission.py                        # full dry run (no hardware)
    python mission.py --mover /dev/ttyUSB0  # real robot, dry-run print
    python mission.py --mover /dev/ttyUSB0 --printer /dev/ttyACM0  # full real

Location modes:
    --mode random    random points within --area radius (default)
    --mode grid      regular grid with --spacing
    --mode wander    each step biased away from recent cluster
"""

import argparse, math, random, time
from mover import Mover
from main  import run_print, DEFAULTS

POOP_DEFAULTS = dict(
    DEFAULTS,
    diameter     = 40.0,
    total_layers = 25,
    layer_height = 0.5,
    bed_temp     = 0,      # no heated bed on the floor
    sensor_source = 'sound',
)


def random_locations(n, area_r, min_spacing, max_attempts=200):
    """Scatter n non-overlapping points within a circle of radius area_r."""
    pts = []
    for _ in range(n):
        for _ in range(max_attempts):
            angle = random.uniform(0, 2 * math.pi)
            r     = area_r * math.sqrt(random.random())  # uniform in disk
            x, y  = r * math.cos(angle), r * math.sin(angle)
            if all(math.hypot(x - px, y - py) >= min_spacing for px, py in pts):
                pts.append((x, y))
                break
        else:
            print(f"  Warning: only found {len(pts)} non-overlapping spots.")
            break
    return pts


def grid_locations(area_r, spacing):
    """Regular grid clipped to a circle."""
    pts = []
    n = int(area_r / spacing)
    for ix in range(-n, n + 1):
        for iy in range(-n, n + 1):
            x, y = ix * spacing, iy * spacing
            if math.hypot(x, y) <= area_r:
                pts.append((x, y))
    random.shuffle(pts)
    return pts


def wander_locations(n, area_r, min_spacing, step_range=(150, 400)):
    """Each point is a random step from the previous, biased outward from clusters."""
    pts = [(0.0, 0.0)]
    x, y = 0.0, 0.0
    for _ in range(n - 1):
        for _ in range(300):
            angle = random.uniform(0, 2 * math.pi)
            dist  = random.uniform(*step_range)
            nx, ny = x + dist * math.cos(angle), y + dist * math.sin(angle)
            if (math.hypot(nx, ny) <= area_r and
                    all(math.hypot(nx - px, ny - py) >= min_spacing for px, py in pts)):
                pts.append((nx, ny))
                x, y = nx, ny
                break
    return pts


# --------------------------------------------------------------------------

def run_mission(mover_port=None, printer_port=None,
                n_poops=5, mode='random', area_r=1500, spacing=250,
                spray_ms=2000, dry_run_print=False, poop_params=None):

    params = poop_params or POOP_DEFAULTS.copy()
    if printer_port:
        import main as m
        m.PORT = printer_port

    mover = Mover(port=mover_port)

    if mode == 'random':
        locations = random_locations(n_poops, area_r, spacing)
    elif mode == 'grid':
        locations = grid_locations(area_r, spacing)[:n_poops]
    elif mode == 'wander':
        locations = wander_locations(n_poops, area_r, spacing)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    print(f"\nMission: {len(locations)} poops, mode={mode}, area_r={area_r}mm")
    print(f"Printer dry_run={dry_run_print}, mover dry_run={mover._dry}\n")

    deposited = []

    for i, (tx, ty) in enumerate(locations):
        dist = math.hypot(tx - mover.x, ty - mover.y)
        print(f"--- Poop {i+1}/{len(locations)}  target=({tx:.0f}, {ty:.0f})  travel={dist:.0f}mm")

        # walk
        mover.drive_to(tx, ty)

        # settle: feet down, spray, let it tack
        # (tacking happens during printer heat-up — no explicit sleep needed)
        mover.park()
        mover.spray(spray_ms)

        # poop
        run_print(p=params, dry_run=dry_run_print)

        # leave
        mover.unpark()
        deposited.append((tx, ty))
        print(f"    deposited. total={len(deposited)}  pos=({mover.x:.0f}, {mover.y:.0f})\n")

    print(f"Mission complete. {len(deposited)} objects deposited.")
    mover.close()
    return deposited


# --------------------------------------------------------------------------

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Roaming printer mission')
    ap.add_argument('--mover',   default=None,     help='ESP32 serial port (omit = dry run)')
    ap.add_argument('--printer', default=None,     help='Ender 3 serial port (omit = dry run)')
    ap.add_argument('--poops',   type=int,   default=5)
    ap.add_argument('--mode',    default='random', choices=['random', 'grid', 'wander'])
    ap.add_argument('--area',    type=float, default=1500, help='Roam radius mm')
    ap.add_argument('--spacing', type=float, default=250,  help='Min spacing between poops mm')
    ap.add_argument('--spray-ms',type=int,   default=2000)
    ap.add_argument('--layers',  type=int,   default=POOP_DEFAULTS['total_layers'])
    ap.add_argument('--diameter',type=float, default=POOP_DEFAULTS['diameter'])
    args = ap.parse_args()

    params = POOP_DEFAULTS.copy()
    params['total_layers'] = args.layers
    params['diameter']     = args.diameter

    run_mission(
        mover_port   = args.mover,
        printer_port = args.printer,
        n_poops      = args.poops,
        mode         = args.mode,
        area_r       = args.area,
        spacing      = args.spacing,
        spray_ms     = args.spray_ms,
        dry_run_print= args.printer is None,
        poop_params  = params,
    )
