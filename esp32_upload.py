#!/usr/bin/env python3
"""
Upload a file to MicroPython via raw REPL over USB serial.
No mpremote needed -- only requires pyserial.

Usage:
    python3 esp32_upload.py /dev/ttyACM1 gen3d_ear_v1.py
    python3 esp32_upload.py /dev/ttyACM1 gen3d_ear_v1.py main.py  # upload as main.py (auto-run on boot)
"""
import serial, time, sys

def upload(port, src_path, dest_name=None):
    if dest_name is None:
        dest_name = src_path.split('/')[-1].split('\')[-1]

    with open(src_path) as f:
        content = f.read()

    print(f'Connecting to {port}...')
    s = serial.Serial(port, 115200, timeout=2)
    time.sleep(0.5)

    # Interrupt any running script
    s.write(b'\x03\x03')
    time.sleep(0.3)
    s.read_all()

    # Enter raw REPL (Ctrl-A)
    s.write(b'\x01')
    time.sleep(0.3)
    banner = s.read_all().decode(errors='replace')
    if 'raw REPL' not in banner:
        print('ERROR: could not enter raw REPL. Is MicroPython flashed?')
        print('Got:', repr(banner))
        s.close()
        sys.exit(1)

    # Write file via exec
    cmd = f"f=open({repr(dest_name)},'w')\nf.write({repr(content)})\nf.close()\nprint('OK')\n"
    s.write(cmd.encode())
    s.write(b'\x04')  # Ctrl-D = execute
    time.sleep(2)
    result = s.read_all().decode(errors='replace')

    # Exit raw REPL (Ctrl-B)
    s.write(b'\x02')
    s.close()

    if 'OK' in result:
        print(f'Uploaded {src_path} -> {dest_name}')
    else:
        print(f'Upload may have failed. Response: {repr(result)}')

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    port     = sys.argv[1]
    src      = sys.argv[2]
    dest     = sys.argv[3] if len(sys.argv) > 3 else None
    upload(port, src, dest)
