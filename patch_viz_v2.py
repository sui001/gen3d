#!/usr/bin/env python3
PATH = "/home/pi/gen3d/viz_app.py"

with open(PATH) as f:
    src = f.read()

changes = []

# 1. VERSION
old = 'VERSION = "2.5"'
new = 'VERSION = "2.6"'
if old in src:
    src = src.replace(old, new, 1); changes.append("VERSION 2.5 -> 2.6")
else:
    print("WARN: VERSION not found")

# 2. Status bar: replace speed span with n + spacing spans
old = '  <span>speed <b id="sSpeed">-</b>mm/s</span>\n'
new = '  <span>n <b id="sNPoints">-</b></span>\n  <span>spacing <b id="sSpacing">-</b>mm</span>\n'
if old in src:
    src = src.replace(old, new, 1); changes.append("status bar: speed -> n + spacing")
else:
    print("WARN: sSpeed span not found")

# 3. Input: replace pSpeedAmp with three spacing inputs
old = '  <label>speed sense amp<input id="pSpeedAmp" type="number" value="0" min="0" max="15" step="0.5"></label>\n'
new = (
    '  <label>spacing min mm<input id="pSpacingMin" type="number" value="3"  min="1" max="10" step="0.5"></label>\n'
    '  <label>spacing max mm<input id="pSpacingMax" type="number" value="5"  min="1" max="10" step="0.5"></label>\n'
    '  <label>spacing sens  <input id="pSpacingSens" type="number" value="20" min="1" max="100" step="1"></label>\n'
)
if old in src:
    src = src.replace(old, new, 1); changes.append("input: pSpeedAmp -> pSpacingMin/Max/Sens")
else:
    print("WARN: pSpeedAmp input not found")

# 4. JS params(): replace speed_amp line with three spacing params
old = '    speed_amp:       parseFloat(document.getElementById(\'pSpeedAmp\').value),\n'
new = (
    '    spacing_min_mm:  parseFloat(document.getElementById(\'pSpacingMin\').value),\n'
    '    spacing_max_mm:  parseFloat(document.getElementById(\'pSpacingMax\').value),\n'
    '    spacing_sens:    parseFloat(document.getElementById(\'pSpacingSens\').value),\n'
)
if old in src:
    src = src.replace(old, new, 1); changes.append("params(): speed_amp -> spacing params")
else:
    print("WARN: params() speed_amp line not found")

# 5. startFreqPoll: replace speed handler with n_pts + spacing
old = "      if (d.speed  !== undefined) document.getElementById('sSpeed').textContent  = d.speed.toFixed(1);\n"
new = (
    "      if (d.n_pts   !== undefined) document.getElementById('sNPoints').textContent = d.n_pts;\n"
    "      if (d.spacing !== undefined) document.getElementById('sSpacing').textContent = d.spacing.toFixed(2);\n"
)
if old in src:
    src = src.replace(old, new, 1); changes.append("startFreqPoll: speed -> n_pts/spacing")
else:
    print("WARN: sSpeed poll line not found")

# 6. saveDefaults: remove pSpeedAmp, add spacing ids
old = "             'pNozzle','pBed','pSAmp','pWobble','pSpeedAmp','pPointSmooth'];\n"
new = "             'pNozzle','pBed','pSAmp','pWobble','pSpacingMin','pSpacingMax','pSpacingSens','pPointSmooth'];\n"
if old in src:
    src = src.replace(old, new, 1); changes.append("saveDefaults: pSpeedAmp -> spacing ids")
else:
    print("WARN: saveDefaults ids not found")

# 7. parse_params: replace speed_amp with three spacing params
old = "    p['speed_amp']     = float(args.get('speed_amp',     p.get('speed_amp', 0.0)))\n"
new = (
    "    p['spacing_min_mm'] = float(args.get('spacing_min_mm', p.get('spacing_min_mm', 3.0)))\n"
    "    p['spacing_max_mm'] = float(args.get('spacing_max_mm', p.get('spacing_max_mm', 5.0)))\n"
    "    p['spacing_sens']   = float(args.get('spacing_sens',   p.get('spacing_sens',  20.0)))\n"
)
if old in src:
    src = src.replace(old, new, 1); changes.append("parse_params: speed_amp -> spacing params")
else:
    print("WARN: parse_params speed_amp not found")

# 8. /voice endpoint: replace _current_speed_mms with n_pts + spacing
old = "            'speed':  round(_main._current_speed_mms[0], 2),\n"
new = (
    "            'n_pts':   _main._current_n_points[0],\n"
    "            'spacing': round(_main._current_spacing_mm[0], 2),\n"
)
if old in src:
    src = src.replace(old, new, 1); changes.append("/voice: speed -> n_pts/spacing")
else:
    print("WARN: /voice speed line not found")

print(f"Applied {len(changes)}/8 changes:")
for c in changes:
    print(" +", c)

if len(changes) == 8:
    with open(PATH, "w") as f:
        f.write(src)
    print("WRITE OK")
else:
    print(f"MISMATCH -- NOT written")
