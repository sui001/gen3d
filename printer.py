import serial, time, re

class Printer:
    def __init__(self, port='/dev/ttyACM0', baud=250000, boot_wait=2.0):
        self.s = serial.Serial(port, baud, timeout=5)
        self.nozzle_temp  = None   # last read actual
        self.nozzle_target = None  # last read target
        self.bed_temp     = None
        self.bed_target   = None
        time.sleep(boot_wait)
        self._drain()

    def _drain(self):
        while self.s.in_waiting:
            self.s.readline()

    def _parse_temps(self, line):
        """Extract temps from lines like: ok T:210.5 /210.0 B:60.1 /60.0"""
        m = re.search(r'T:([\d.]+)\s*/\s*([\d.]+)', line)
        if m:
            self.nozzle_temp   = float(m.group(1))
            self.nozzle_target = float(m.group(2))
        m = re.search(r'B:([\d.]+)\s*/\s*([\d.]+)', line)
        if m:
            self.bed_temp   = float(m.group(1))
            self.bed_target = float(m.group(2))

    def send(self, cmd):
        """Send one gcode line, block until 'ok' response."""
        line = (cmd.strip() + '\n').encode()
        self.s.write(line)
        while True:
            resp = self.s.readline().decode(errors='replace').strip()
            if resp:
                self._parse_temps(resp)
            if resp.startswith('ok'):
                return resp
            if resp.startswith('Error'):
                raise RuntimeError(f"Printer error: {resp}")

    def close(self):
        self.s.close()
