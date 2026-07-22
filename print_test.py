#!/usr/bin/env python3
"""First funsies ceramic print: 40mm dia x 10mm single-wall cylinder."""
import serial, math, time

PORT = '/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10JKQMX-if00-port0'
CX, CY = 180.0, 180.0          # bed centre (360x360)
DIAM = 40.0
R = DIAM / 2
LAYER_H = 1.0
LAYERS = 10
LINE_W = 2.5
NPTS = 72
BARREL_AREA = math.pi * (93.0 / 2) ** 2   # 6792.9 mm^2
FLOW = 1.2
E_PER_MM = (LINE_W * LAYER_H) / BARREL_AREA * FLOW   # rod mm per mm of XY path
F_PRINT = 360      # 6 mm/s XY
F_TRAVEL = 1000
PRIME_XY = (150.0, 180.0)      # off to the side, clear of the circle
PRIME_ROD = 0.24               # rod mm for the prime blob

s = serial.Serial(PORT, BAUD := 250000, timeout=2)
time.sleep(12)
while s.in_waiting:
    s.readline()

def cmd(c, wait=180):
    s.write((c + '\n').encode())
    t0 = time.time()
    while time.time() - t0 < wait:
        line = s.readline().decode(errors='replace').strip()
        if line.startswith('ok') or line.startswith('Error'):
            return
def drain():
    while s.in_waiting: s.readline()

cmd('M999', 10)
drain()
cmd('T0'); cmd('M302 P1')
cmd('G21'); cmd('G90'); cmd('M83')   # abs XYZ, relative E

print('homing...')
cmd('G28', 120)

print('prime blob...')
cmd('G0 Z5 F{}'.format(F_TRAVEL))
cmd('G0 X{} Y{} F{}'.format(PRIME_XY[0], PRIME_XY[1], F_TRAVEL))
cmd('G1 Z{} F{}'.format(LAYER_H, F_TRAVEL))
cmd('G1 E{:.4f} F6'.format(PRIME_ROD), 60)   # stationary charge/blob, slow

# points around the circle
pts = [(CX + R*math.cos(2*math.pi*i/NPTS), CY + R*math.sin(2*math.pi*i/NPTS))
       for i in range(NPTS)]
pts_closed = pts + [pts[0]]

print('printing {} layers...'.format(LAYERS))
for layer in range(1, LAYERS + 1):
    z = layer * LAYER_H
    # travel to start of this layer
    cmd('G0 X{:.3f} Y{:.3f} F{}'.format(pts[0][0], pts[0][1], F_TRAVEL))
    cmd('G1 Z{:.3f} F{}'.format(z, F_TRAVEL))
    for (x1, y1), (x2, y2) in zip(pts_closed, pts_closed[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        e = seg * E_PER_MM
        cmd('G1 X{:.3f} Y{:.3f} E{:.5f} F{}'.format(x2, y2, e, F_PRINT))
    print('  layer {}/{} z={:.1f}'.format(layer, LAYERS, z))

print('done -- lift straight up 10mm, stay over the print (hose reach)')
cmd('G91')
cmd('G1 Z10 F{}'.format(F_TRAVEL))   # relative up, no X/Y travel
cmd('G90')
cmd('M84')
s.close()
print('COMPLETE')
