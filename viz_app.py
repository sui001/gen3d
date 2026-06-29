# -*- coding: utf-8 -*-
from flask import Flask, Response, request
import json, time, threading, sys
sys.path.insert(0, '/home/pi/gen3d')

app = Flask(__name__)
_stop_flag = threading.Event()
_cmd_queue = []          # real-time commands injected mid-print
_cmd_lock = threading.Lock()
_printer_ref = [None]    # live reference to Printer object during a print

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>gen3d</title>
<style>
* { box-sizing: border-box; }
body { background:#111; color:#ccc; font-family:monospace;
       margin:0; padding:16px; display:flex; flex-direction:column; align-items:center; gap:10px; }
h2 { margin:0; font-size:16px; color:#aaa; }
canvas { border:1px solid #2a2a2a; background:#0d0d0d; }
#statusbar { display:flex; gap:20px; font-size:12px; flex-wrap:wrap; justify-content:center; }
#statusbar span { color:#666; }
#statusbar span b { color:#ccc; }
#direction { font-size:18px; width:20px; display:inline-block; text-align:center; }
.params { display:grid; grid-template-columns:repeat(4,1fr); gap:5px 14px; font-size:11px; width:500px; }
.params label { display:flex; flex-direction:column; gap:2px; color:#777; }
.params input { background:#1a1a1a; color:#ccc; border:1px solid #333;
                padding:3px 5px; font-family:monospace; font-size:11px; width:100%; }
.controls { display:flex; gap:8px; }
button { background:#222; color:#bbb; border:1px solid #444;
         padding:5px 14px; cursor:pointer; font-family:monospace; font-size:12px; }
button:hover { background:#2a2a2a; border-color:#666; }
button:disabled { opacity:0.35; cursor:default; }
#btnPrint { border-color:#4a6; color:#8d8; }
#btnStop  { border-color:#a44; color:#d88; }
</style>
</head>
<body>
<h2>gen3d <span style="font-size:10px;color:#444">v1.4</span></h2>
<canvas id="c" width="500" height="500"></canvas>

<div id="statusbar">
  <span>layer <b id="sLayer">—</b></span>
  <span>z <b id="sZ">—</b>mm</span>
  <span>cpu <b id="sTemp">—</b>°C <span id="direction">—</span></span>
  <span>eta <b id="sEta">—</b></span>
  <span>r&#916; <b id="sDelta">—</b>mm</span>
</div>

<div class="params">
  <label>sides (0=circle)<input id="pSides"   type="number" value="0"   min="0" max="12"></label>
  <label>diameter mm<input      id="pDiam"    type="number" value="50"  min="10" max="150"></label>
  <label>n points<input         id="pPoints"  type="number" value="72"  min="12" max="360"></label>
  <label>nozzle dia mm<input    id="pNozzleDia" type="number" value="1.5" step="0.1" min="0.2" max="2.0" oninput="validate()"></label>
  <label>line width mm<input    id="pLine"    type="number" value="2" step="0.1" min="0.2" max="2.0" oninput="validate()"></label>
  <label>layer height mm<input  id="pLayerH"  type="number" value="0.7" step="0.1" min="0.1" max="2.0" oninput="validate()"></label>
  <label>total layers<input     id="pLayers"  type="number" value="40"  min="1" max="500"></label>
  <label>base layers<input      id="pBase"    type="number" value="1"   min="0" max="20"></label>
  <label>max overhang %<input   id="pOverhang" type="number" value="40" min="5" max="80"></label>
  <label>print speed mm/s<input id="pSpeed"   type="number" value="15"  min="5" max="80" oninput="validate();document.getElementById('speedSlider').value=this.value;document.getElementById('speedVal').textContent=this.value+'mm/s'"></label>
  <label>flow %<input           id="pFlow"    type="number" value="100" min="1" max="1000" oninput="syncFlowSlider(this.value)"></label>
  <label>nozzle temp C<input    id="pNozzle"  type="number" value="210" min="150" max="280" oninput="document.getElementById('tempSlider').value=this.value;document.getElementById('tempVal').textContent=this.value+'°C'"></label>
  <label>bed temp C<input       id="pBed"     type="number" value="60"  min="0" max="110"></label>
  <label>sensor centre °C<input id="pSCentre" type="number" value="48"  min="0" max="100" step="0.5"></label>
  <label>sensor range °C<input  id="pSRange"  type="number" value="10"  min="1" max="80"  step="0.5"></label>
  <label>sensor amp mm<input    id="pSAmp"    type="number" value="5"   min="0.1" max="30" step="0.5"></label>
  <label>sensor source<select id="pSSource" onchange="onSensorSourceChange()" style="background:#1a1a1a;color:#ccc;border:1px solid #333;padding:3px 5px;font-family:monospace;font-size:11px;width:100%"><option value="cpu">cpu temp</option><option value="sound" selected>sound (ESP32)</option></select></label>
</div>
<div id="warnings" style="font-size:11px;color:#c66;min-height:14px;text-align:center"></div>

<div style="display:grid;grid-template-columns:80px 1fr 50px;align-items:center;gap:6px 10px;font-size:11px;color:#777;width:500px">
  <span>live flow</span>
  <input id="flowSlider" type="range" min="1" max="1000" value="100" oninput="setFlowLive(this.value)">
  <span id="flowVal" style="color:#ccc">100%</span>

  <span>nozzle temp</span>
  <input id="tempSlider" type="range" min="150" max="280" value="210" oninput="setTempLive(this.value)">
  <span id="tempVal" style="color:#ccc">210°C</span>

  <span>speed override</span>
  <input id="speedSlider" type="range" min="10" max="300" value="100" oninput="setSpeedLive(this.value)">
  <span id="speedVal" style="color:#ccc">100%</span>
</div>

<div class="controls">
  <button id="btnViz"   onclick="startViz()">preview</button>
  <button id="btnPrint" onclick="startPrint()">start print</button>
  <button id="btnStop"  onclick="stopAll(true)" disabled>stop</button>
</div>

<script>
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const W = canvas.width, H = canvas.height;
const CX = W/2, CY = H/2;
let SCALE = 4.5;
let es = null, layerCount = 0;
let layerStartTime = null, lastPt = null;

function params() {
  return {
    sides:        parseInt(document.getElementById('pSides').value),
    diameter:     parseFloat(document.getElementById('pDiam').value),
    n_points:     parseInt(document.getElementById('pPoints').value),
    nozzle_dia:    parseFloat(document.getElementById('pNozzleDia').value),
    sensor_centre: parseFloat(document.getElementById('pSCentre').value),
    sensor_range:  parseFloat(document.getElementById('pSRange').value),
    sensor_amp:    parseFloat(document.getElementById('pSAmp').value),
    sensor_source: document.getElementById('pSSource').value,
    line_width:   parseFloat(document.getElementById('pLine').value),
    layer_height: parseFloat(document.getElementById('pLayerH').value),
    total_layers: parseInt(document.getElementById('pLayers').value),
    base_layers:  parseInt(document.getElementById('pBase').value),
    max_overhang: parseFloat(document.getElementById('pOverhang').value) / 100,
    print_speed:  parseFloat(document.getElementById('pSpeed').value),
    flow_pct:     parseInt(document.getElementById('pFlow').value),
    nozzle_temp:  parseInt(document.getElementById('pNozzle').value),
    bed_temp:     parseInt(document.getElementById('pBed').value),
  };
}

function validate() {
  const lw     = parseFloat(document.getElementById('pLine').value);
  const lh     = parseFloat(document.getElementById('pLayerH').value);
  const spd    = parseFloat(document.getElementById('pSpeed').value);
  const nozzle = parseFloat(document.getElementById('pNozzleDia').value);
  const MAX_FLOW = 10.0; // mm³/s safe limit for UM2+ Bowden

  const flow = spd * lw * lh;
  const warnings = [];

  const red   = '#c44';
  const clear = '';

  // Volumetric flow
  const flowOver = flow > MAX_FLOW;
  const flowWarn = flow > MAX_FLOW * 0.8;
  document.getElementById('pSpeed').style.borderColor  = flowOver ? red : (flowWarn ? '#a84' : clear);
  document.getElementById('pLine').style.borderColor   = flowOver ? red : clear;
  document.getElementById('pLayerH').style.borderColor = flowOver ? red : clear;
  if (flowOver) warnings.push('volumetric flow ' + flow.toFixed(1) + ' mm³/s — max ~10 (reduce speed or layer height)');
  else if (flowWarn) warnings.push('flow ' + flow.toFixed(1) + ' mm³/s — approaching limit');

  // Layer height vs line width
  if (lh > lw * 0.8) {
    document.getElementById('pLayerH').style.borderColor = red;
    warnings.push('layer height > 80% of line width — risk of poor bonding');
  }

  // Flow % warning
  const flow_pct = parseInt(document.getElementById('pFlow').value);
  if (flow_pct > 200) {
    document.getElementById('pFlow').style.borderColor = '#a84';
    warnings.push('flow ' + flow_pct + '% — watch for over-extrusion');
  }

  // Line width vs nozzle
  if (lw < nozzle * 0.6) {
    document.getElementById('pLine').style.borderColor = red;
    warnings.push('line width < 60% of nozzle diameter (0.8mm) — likely to clog');
  }
  if (lw > nozzle * 2.0) {
    document.getElementById('pLine').style.borderColor = red;
    warnings.push('line width > 2× nozzle — poor definition');
  }

  document.getElementById('warnings').textContent = warnings.join('  |  ');
}

function syncFlowSlider(val) {
  document.getElementById('flowSlider').value = val;
  document.getElementById('flowVal').textContent = val + '%';
}

function setFlowLive(val) {
  document.getElementById('pFlow').value = val;
  document.getElementById('flowVal').textContent = val + '%';
  fetch('/set_flow?pct=' + val, {method:'POST'});
}

function setTempLive(val) {
  document.getElementById('pNozzle').value = val;
  document.getElementById('tempVal').textContent = val + '°C';
  fetch('/set_temp?c=' + val, {method:'POST'});
}

function setSpeedLive(val) {
  document.getElementById('speedVal').textContent = val + '%';
  fetch('/set_speed?pct=' + val, {method:'POST'});
}

let tempPollInterval = null;
function startTempPoll() {
  if (tempPollInterval) return;
  tempPollInterval = setInterval(function() {
    fetch('/get_temps').then(r => r.json()).then(d => {
      if (d.nozzle !== null)
        document.getElementById('tempVal').textContent =
          d.nozzle.toFixed(1) + '/' + (d.nozzle_target||'?') + '°C';
    }).catch(()=>{});
  }, 2000);
}
function stopTempPoll() {
  if (tempPollInterval) { clearInterval(tempPollInterval); tempPollInterval = null; }
}

function onSensorSourceChange() {
  const isCpu = document.getElementById("pSSource").value === "cpu";
  ["pSCentre","pSRange"].forEach(function(id) {
    document.getElementById(id).parentElement.style.opacity = isCpu ? "1" : "0.3";
    document.getElementById(id).disabled = !isCpu;
  });
}
onSensorSourceChange();

// Run on load
validate();

function toScreen(x, y) {
  return [CX + (x - 100) * SCALE, CY - (y - 100) * SCALE];
}

function drawPoint(x, y, layer, alpha) {
  const [sx, sy] = toScreen(x, y);
  const hue = (layer * 4) % 360;
  ctx.strokeStyle = 'hsla(' + hue + ',70%,65%,' + alpha + ')';
  if (lastPt) {
    ctx.beginPath();
    ctx.moveTo(lastPt[0], lastPt[1]);
    ctx.lineTo(sx, sy);
    ctx.stroke();
  }
  lastPt = [sx, sy];
}

function startLayer(layer) {
  lastPt = null;
  ctx.lineWidth = 1.2;
  layerStartTime = Date.now();
}

function updateStatus(d) {
  if (d.layer !== undefined) document.getElementById('sLayer').textContent =
    d.layer + (d.is_base ? ' [base]' : '');
  if (d.z !== undefined) document.getElementById('sZ').textContent = d.z.toFixed(2);
  if (d.temp !== undefined) {
      const isSound = document.getElementById("pSSource").value === "sound";
      document.getElementById("sSrcLabel").textContent = isSound ? "sound" : "cpu";
      document.getElementById("sTempUnit").textContent = isSound ? "mm" : "�C";
      document.getElementById("sTemp").textContent = d.temp.toFixed(isSound ? 2 : 1);
    }
  if (d.radius_delta !== undefined) {
    const rd = d.radius_delta;
    document.getElementById('direction').textContent = rd > 0.01 ? '▲' : rd < -0.01 ? '▼' : '─';
    document.getElementById('sDelta').textContent = (rd >= 0 ? '+' : '') + rd.toFixed(3);
  }
  if (d.eta_s !== undefined) {
    const s = Math.round(d.eta_s);
    const m = Math.floor(s / 60), sec = s % 60;
    document.getElementById('sEta').textContent = m + ':' + String(sec).padStart(2,'0');
  }
}

function stopAll(sendStop) {
  if (sendStop) fetch('/stop', {method:'POST'});
  if (es) { es.close(); es = null; }
  document.getElementById('btnViz').disabled = false;
  document.getElementById('btnPrint').disabled = false;
  document.getElementById('btnStop').disabled = true;
  stopTempPoll();
}

function openStream(url) {
  stopAll(false);
  ctx.clearRect(0, 0, W, H);
  layerCount = 0; lastPt = null;
  SCALE = 200 / (parseFloat(document.getElementById('pDiam').value) / 2 + 12);
  document.getElementById('btnViz').disabled = true;
  document.getElementById('btnPrint').disabled = true;
  document.getElementById('btnStop').disabled = false;
  document.getElementById('sLayer').textContent = '—';
  document.getElementById('sEta').textContent = '—';

  es = new EventSource(url);
  es.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.done)  { document.getElementById('sEta').textContent = 'done'; stopAll(false); return; }
    if (d.error) { document.getElementById('sLayer').textContent = 'err: '+d.error; stopAll(false); return; }

    if (d.type === 'layer_start') {
      layerCount++;
      startLayer(layerCount);
      updateStatus(d);
    } else if (d.type === 'point') {
      drawPoint(d.x, d.y, layerCount, Math.max(0.15, 1 - layerCount * 0.008));
    } else if (d.type === 'layer_end') {
      updateStatus(d);
      lastPt = null;
    }
  };
  es.onerror = function() { stopAll(false); };
}

function startViz()   { openStream('/viz_stream?' + new URLSearchParams(params())); }
function startPrint() {
  if (!confirm('Start print? Confirm: bed clear, filament loaded.')) return;
  startTempPoll();
  openStream('/print_stream?' + new URLSearchParams(params()));
}
</script>
</body>
</html>"""


def parse_params(args):
    from main import DEFAULTS
    p = DEFAULTS.copy()
    p['sides']        = int(args.get('sides',        p['sides']))
    p['diameter']     = float(args.get('diameter',   p['diameter']))
    p['n_points']     = int(args.get('n_points',     p['n_points']))
    p['nozzle_dia']     = float(args.get('nozzle_dia',     p['nozzle_dia']))
    p['sensor_centre']  = float(args.get('sensor_centre',  p.get('sensor_centre', 50.0)))
    p['sensor_range']   = float(args.get('sensor_range',   p.get('sensor_range',  40.0)))
    p['sensor_amp']     = float(args.get('sensor_amp',     p.get('sensor_amp',     5.0)))
    p['sensor_source'] = args.get('sensor_source', 'cpu')
    p['line_width']   = float(args.get('line_width', p['line_width']))
    p['layer_height'] = float(args.get('layer_height', p['layer_height']))
    p['total_layers'] = int(args.get('total_layers', p['total_layers']))
    p['base_layers']  = int(args.get('base_layers',  p['base_layers']))
    p['max_overhang'] = float(args.get('max_overhang', p['max_overhang']))
    p['print_speed']  = float(args.get('print_speed', p['print_speed']))
    p['flow_pct']     = int(args.get('flow_pct',     p.get('flow_pct', 100)))
    p['nozzle_temp']  = int(args.get('nozzle_temp',  p['nozzle_temp']))
    p['bed_temp']     = int(args.get('bed_temp',     p['bed_temp']))
    return p


def stream_run(p, dry_run=False):
    """Generator that yields SSE strings, running print/preview in a thread."""
    import queue
    q = queue.Queue()

    def on_layer(layer, pts, z, samples, temp, is_base, radius_delta, eta_s):
        q.put({'type': 'layer_start', 'layer': layer, 'z': round(z, 3),
               'temp': round(temp, 1), 'is_base': is_base})

    def on_point(x, y):
        q.put({'type': 'point', 'x': round(x, 3), 'y': round(y, 3)})

    def on_layer_end(layer, pts, z, samples, temp, is_base, radius_delta, eta_s):
        q.put({'type': 'layer_end', 'layer': layer, 'z': round(z, 3),
               'temp': round(temp, 1), 'radius_delta': round(radius_delta, 4),
               'eta_s': round(eta_s, 1), 'is_base': is_base})

    # Wrap on_layer to fire both start and end events
    def on_layer_combined(layer, pts, z, samples, temp, is_base, radius_delta, eta_s):
        on_layer(layer, pts, z, samples, temp, is_base, radius_delta, eta_s)
        # end event posted after points are generated — use sentinel
        q.put({'type': 'layer_end', 'layer': layer, 'z': round(z, 3),
               'temp': round(temp, 1), 'radius_delta': round(radius_delta, 4),
               'eta_s': round(eta_s, 1), 'is_base': is_base})

    err = [None]

    def do_run():
        try:
            from main import run_print
            run_print(p=p, dry_run=dry_run, on_layer=on_layer_combined,
                      on_point=on_point, sensor_fn=None,
                      stop_flag=_stop_flag)
        except Exception as ex:
            err[0] = str(ex)
        finally:
            q.put(None)  # sentinel

    t = threading.Thread(target=do_run, daemon=True)
    t.start()

    while True:
        item = q.get()
        if item is None:
            break
        yield 'data: ' + json.dumps(item) + '\n\n'

    if err[0]:
        yield 'data: ' + json.dumps({'error': err[0]}) + '\n\n'
    else:
        yield 'data: {"done":true}\n\n'


@app.route('/')
def index():
    return HTML


@app.route('/stop', methods=['POST'])
def stop():
    _stop_flag.set()
    return ('', 204)


@app.route('/set_temp', methods=['POST'])
def set_temp():
    try:
        c = max(150, min(280, int(request.args.get('c', '210'))))
        if _printer_ref[0]:
            _printer_ref[0].send(f'M104 S{c}')
    except:
        pass
    return ('', 204)


@app.route('/set_speed', methods=['POST'])
def set_speed():
    try:
        pct = max(10, min(300, int(request.args.get('pct', '100'))))
        if _printer_ref[0]:
            _printer_ref[0].send(f'M220 S{pct}')
    except:
        pass
    return ('', 204)


@app.route('/get_temps')
def get_temps():
    p = _printer_ref[0]
    return json.dumps({
        'nozzle':        p.nozzle_temp   if p else None,
        'nozzle_target': p.nozzle_target if p else None,
        'bed':           p.bed_temp      if p else None,
        'bed_target':    p.bed_target    if p else None,
    })


@app.route('/set_flow', methods=['POST'])
def set_flow():
    pct = request.args.get('pct', '100')
    try:
        pct = max(1, min(1000, int(pct)))
        with _cmd_lock:
            _cmd_queue.append(f'M221 S{pct}')
        # also send directly if printer is live
        if _printer_ref[0]:
            try:
                _printer_ref[0].send(f'M221 S{pct}')
            except:
                pass
    except ValueError:
        pass
    return ('', 204)


@app.route('/viz_stream')
def viz_stream():
    p = parse_params(request.args)
    _stop_flag.clear()
    return Response(stream_run(p, dry_run=True), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/print_stream')
def print_stream():
    p = parse_params(request.args)
    _stop_flag.clear()
    return Response(stream_run(p, dry_run=False), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)



