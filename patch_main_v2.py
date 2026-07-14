#!/usr/bin/env python3
PATH = "/home/pi/gen3d/main.py"

with open(PATH) as f:
    src = f.read()

changes = []

old = 'VERSION = "1.15"'
new = 'VERSION = "1.16"'
if old in src:
    src = src.replace(old, new, 1); changes.append("VERSION 1.15 -> 1.16")
else:
    print("WARN: VERSION not found")

old = '_current_speed_mms = [0.0]'
new = '_current_n_points  = [0]\n_current_spacing_mm = [0.0]'
if old in src:
    src = src.replace(old, new, 1); changes.append("renamed _current_speed_mms")
else:
    print("WARN: _current_speed_mms not found")

old = '    sensor_amp    = 5.0,\n)'
new = '    sensor_amp    = 5.0,\n    spacing_min_mm = 3.0,\n    spacing_max_mm = 5.0,\n    spacing_sens   = 20.0,\n)'
if old in src:
    src = src.replace(old, new, 1); changes.append("DEFAULTS spacing params")
else:
    print("WARN: DEFAULTS sensor_amp not found")

old = "diag_f.write('layer,point_idx,angle_deg,voicex_raw,voicey_raw,radius,speed_mms\\n')"
new = "diag_f.write('layer,point_idx,angle_deg,voicex_raw,voicey_raw,radius,n_layer\\n')"
if old in src:
    src = src.replace(old, new, 1); changes.append("diag header speed_mms -> n_layer")
else:
    print("WARN: diag header not found")

old = '    prev_mean_r = radius\n\n    for layer in range'
new = "    prev_mean_r = radius\n    n_next = p['n_points']  # updated each layer from voiceY\n\n    for layer in range"
if old in src:
    src = src.replace(old, new, 1); changes.append("n_next init before loop")
else:
    print("WARN: n_next init pattern not found")

old = "            n = p['n_points']\n\n            # point 0 first"
new = "            n = n_next\n            path.set_n(n)\n\n            # point 0 first"
if old in src:
    src = src.replace(old, new, 1); changes.append("n = n_next + path.set_n(n)")
else:
    print("WARN: n = p['n_points'] not found")

old = '        prev_mean_r = mean_r\n\n        if on_layer:'
new = (
    '        prev_mean_r = mean_r\n'
    '        vy_abs       = abs(_current_voice_y[0])\n'
    "        spacing_sens = p.get('spacing_sens', 20.0)\n"
    "        spacing_min  = p.get('spacing_min_mm', 3.0)\n"
    "        spacing_max  = p.get('spacing_max_mm', 5.0)\n"
    '        t_vy         = min(1.0, vy_abs / max(0.001, spacing_sens))\n'
    '        spacing_mm   = spacing_max - t_vy * (spacing_max - spacing_min)\n'
    '        n_next       = max(6, round(2 * math.pi * mean_r / spacing_mm))\n'
    '        _current_n_points[0]   = n_next\n'
    '        _current_spacing_mm[0] = round(spacing_mm, 2)\n'
    '\n'
    '        if on_layer:'
)
if old in src:
    src = src.replace(old, new, 1); changes.append("n_next computation after mean_r")
else:
    print("WARN: n_next computation pattern not found")

print(f"Applied {len(changes)}/7 changes:")
for c in changes:
    print(" +", c)

if len(changes) == 7:
    with open(PATH, "w") as f:
        f.write(src)
    print("WRITE OK")
else:
    print(f"MISMATCH -- NOT written")
