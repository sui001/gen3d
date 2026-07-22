#!/usr/bin/env python3
"""
jog.py -- interactive keyboard jog console for the CREEP / Groundskeeper printer.

Keeps ONE serial connection open the whole time, so:
  - the board is only reset once (on connect), never between moves
  - moves fire in milliseconds -- no 12s settle, no round-trip lag

Run on the Pi:  python3 jog.py
(ssh pi@groundskeeper, then run it)

KEYS
  up / down     extrude / retract E by the current E step
  space         quick prime: extrude one BIG step (5x current)
  [ / ]         decrease / increase E step size
  w / s         nozzle up / down (Z) by the current Z step
  , / .         decrease / increase Z step size
  h             home all (G28)   -- asks for confirm
  0             go to Z0 (first-layer height, after homing)
  e             toggle motors enabled/disabled (M17/M84)
  p             print current position (M114)
  q             quit
"""
import sys, time, termios, tty, select
import serial

PORT = '/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10JKQMX-if00-port0'
BAUD = 250000

E_STEPS = [0.05, 0.1, 0.2, 0.4, 0.8, 1.5]
Z_STEPS = [0.05, 0.1, 0.5, 1.0, 5.0, 10.0]
E_FEED  = 6      # mm/min for E. F12 is the AVR-crash ceiling; F6 gives more torque
                 # under nozzle backpressure (stepper torque sags at higher step rates)
Z_FEED  = 300    # mm/min for Z

e_idx = 2   # start at 0.2mm E
z_idx = 3   # start at 1.0mm Z
e_pos = 0.0


def connect():
    s = serial.Serial(PORT, BAUD, timeout=2)
    time.sleep(2)
    # drain boot chatter
    t0 = time.time()
    while time.time() - t0 < 10:
        line = s.readline().decode(errors='replace').strip()
        if 'ready' in line.lower() or line.startswith('ok') or 'RTS' in line:
            break
    while s.in_waiting:
        s.readline()
    return s


def send(s, cmd, wait_ok=True, timeout=60):
    s.write((cmd + '\n').encode())
    if not wait_ok:
        return ''
    t0 = time.time()
    out = []
    while time.time() - t0 < timeout:
        line = s.readline().decode(errors='replace').strip()
        if line:
            out.append(line)
        if line.startswith('ok') or line.startswith('Error'):
            break
    return ' | '.join(out)


def getpos(s):
    while s.in_waiting:
        s.readline()
    s.write(b'M114\n')
    t0 = time.time()
    while time.time() - t0 < 3:
        line = s.readline().decode(errors='replace').strip()
        if line.startswith('X:'):
            return line
    return '?'


def status():
    sys.stdout.write('\r\033[K'
        'E step {:<5}  Z step {:<5}  E pos {:+.3f}   '
        '[up/down=E  space=prime  [/]=Estep  w/s=Z  ,/.=Zstep  h=home  0=Z0  e=en  p=pos  q=quit]'
        .format(E_STEPS[e_idx], Z_STEPS[z_idx], e_pos))
    sys.stdout.flush()


def main():
    global e_idx, z_idx, e_pos
    print('connecting to', PORT, '...')
    s = connect()
    print('connected. clearing any stop state (M999)...')
    send(s, 'M999', timeout=10)
    send(s, 'T0'); send(s, 'M302 P1')   # select E, allow cold extrude
    send(s, 'G91')                       # relative moves for jogging
    enabled = True
    send(s, 'M17')
    print('ready. drive with the keys below.\n')

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        status()
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not r:
                continue
            c = sys.stdin.read(1)

            if c == '\x1b':  # escape seq (arrow keys)
                seq = sys.stdin.read(2)
                if seq == '[A':      # up = extrude
                    send(s, 'G1 E{:.3f} F{}'.format(E_STEPS[e_idx], E_FEED))
                    e_pos += E_STEPS[e_idx]
                elif seq == '[B':    # down = retract
                    send(s, 'G1 E{:.3f} F{}'.format(-E_STEPS[e_idx], E_FEED))
                    e_pos -= E_STEPS[e_idx]
            elif c == ' ':           # prime = 5x extrude
                big = E_STEPS[e_idx] * 5
                send(s, 'G1 E{:.3f} F{}'.format(big, E_FEED))
                e_pos += big
            elif c == '[':
                e_idx = max(0, e_idx - 1)
            elif c == ']':
                e_idx = min(len(E_STEPS) - 1, e_idx + 1)
            elif c == 'w':           # nozzle up
                send(s, 'G1 Z{:.3f} F{}'.format(Z_STEPS[z_idx], Z_FEED))
            elif c == 's':           # nozzle down
                send(s, 'G1 Z{:.3f} F{}'.format(-Z_STEPS[z_idx], Z_FEED))
            elif c == ',':
                z_idx = max(0, z_idx - 1)
            elif c == '.':
                z_idx = min(len(Z_STEPS) - 1, z_idx + 1)
            elif c == 'h':
                sys.stdout.write('\r\033[Khome all axes? (y/n) '); sys.stdout.flush()
                if sys.stdin.read(1) == 'y':
                    send(s, 'G90'); send(s, 'G28', timeout=120); send(s, 'G91')
            elif c == '0':
                send(s, 'G90'); send(s, 'G1 Z0 F{}'.format(Z_FEED), timeout=30); send(s, 'G91')
            elif c == 'e':
                enabled = not enabled
                send(s, 'M17' if enabled else 'M84')
            elif c == 'p':
                sys.stdout.write('\r\033[K' + getpos(s) + '\n'); sys.stdout.flush()
            elif c == 'q':
                break
            status()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        s.close()
        print('\nclosed.')


if __name__ == '__main__':
    main()
