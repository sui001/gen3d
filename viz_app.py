VERSION = "3.1"
# -*- coding: utf-8 -*-
from flask import Flask, Response, request
import json, time, threading, sys
sys.path.insert(0, '/home/pi/gen3d')

app = Flask(__name__)
_stop_flag = threading.Event()
_cmd_queue = []          # real-time commands injected mid-print
_cmd_lock = threading.Lock()
_printer_ref = [None]    # live reference to Printer object during a print

# ── idle jog / pre-prime state (browser jog console) ─────────────────────────
_jog_printer = [None]        # Printer held open while jogging (idle only)
_jog_lock    = threading.Lock()
_jog_e_pos   = [0.0]         # cumulative rod mm since connect/home

# ── broadcast state (survives browser refresh mid-print) ─────────────────────
_print_active   = threading.Event()   # set while a print/viz thread is running
_broadcast_qs   = []                  # one Queue per connected SSE client
_broadcast_lock = threading.Lock()
_last_layer_evt = [None]              # most-recent layer_end → replayed on reconnect

def _broadcast(evt):
    """Push event to every subscribed SSE client."""
    if evt is not None and isinstance(evt, dict) and evt.get('type') == 'layer_end':
        _last_layer_evt[0] = evt
    with _broadcast_lock:
        for q in list(_broadcast_qs):
            try:
                q.put_nowait(evt)
            except Exception:
                pass  # queue full (slow client) — drop

def _sub():
    import queue as _qmod
    q = _qmod.Queue(maxsize=1000)
    with _broadcast_lock:
        _broadcast_qs.append(q)
    return q

def _unsub(q):
    with _broadcast_lock:
        try:
            _broadcast_qs.remove(q)
        except ValueError:
            pass

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
#btnRecon { border-color:#46a; color:#88d; display:none; }
</style>
</head>
<body>
<button id="btnEstop" onclick="estop()" title="stop host streaming + fire M112 (power switch is the ultimate stop)"
        style="position:fixed;top:10px;right:10px;z-index:100;border:2px solid #e33;color:#fff;background:#a11;font-weight:bold;font-size:14px;padding:10px 20px;cursor:pointer">&#9632; E-STOP</button>
<h2>gen3d <span style="font-size:10px;color:#444">{VERSION}</span></h2>
<canvas id="c" width="500" height="500"></canvas>

<div id="statusbar">
  <span>layer <b id="sLayer">?</b></span>
  <span>z <b id="sZ">?</b>mm</span>
  <span>voice <b id="sTemp">?</b>mm <span id="direction">?</span></span>
  <span>vX <b id="sVoiceX">-</b></span>
  <span>vY <b id="sVoiceY">-</b></span>
  <span>freq <b id="sFreq">&mdash;</b>Hz</span>
  <span>n <b id="sNPoints">-</b></span>
  <span>spacing <b id="sSpacing">-</b>mm</span>
  <span>eta <b id="sEta">?</b></span>
  <span>r&#916; <b id="sDelta">?</b>mm</span>
</div>
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
  <label>max overhang %<input   id="pOverhang" type="number" value="40" min="5" max="100"></label>
  <label>print speed mm/s<input id="pSpeed"   type="number" value="15"  min="5" max="80" oninput="validate();document.getElementById('speedSlider').value=this.value;document.getElementById('speedVal').textContent=this.value+'mm/s'"></label>
  <label>flow %<input           id="pFlow"    type="number" value="100" min="1" max="1000" oninput="syncFlowSlider(this.value)"></label>
  <label>nozzle temp C<input    id="pNozzle"  type="number" value="210" min="150" max="280" oninput="document.getElementById('tempSlider').value=this.value;document.getElementById('tempVal').textContent=this.value+'°C'"></label>
  <label>bed temp C<input       id="pBed"     type="number" value="60"  min="0" max="110"></label>
  <label>sensor amp mm<input    id="pSAmp"    type="number" value="5"   min="0.1" max="80" step="1"></label>
  <label>wobble mm<input id="pWobble" type="number" value="0" min="0" max="20" step="0.5"></label>
  <label>spacing min mm<input id="pSpacingMin" type="number" value="3"  min="1" max="10" step="0.5"></label>
  <label>spacing max mm<input id="pSpacingMax" type="number" value="5"  min="1" max="10" step="0.5"></label>
  <label>spacing sens  <input id="pSpacingSens" type="number" value="20" min="1" max="100" step="1"></label>
  <label>point smooth mm<input id="pPointSmooth" type="number" value="2" min="0.2" max="20" step="0.2"></label>
  <label>pitch centre Hz<input id="pPitchCentre" type="number" value="500" min="50" max="5000" step="10"></label>
  <label>pitch range Hz<input  id="pPitchRange"  type="number" value="400" min="50" max="4000" step="10"></label>
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
  <button id="btnRecon" onclick="reconnect()">reconnect</button>
  <button id="btnStop"  onclick="stopAll(true)" disabled>stop</button>
  <button id="btnSave" onclick="saveDefaults()" style="border-color:#46a;color:#88d;font-size:11px;padding:4px 10px;">save as default</button>
</div>

<div id="jogPanel" style="border:1px solid #333;padding:10px;width:500px;display:flex;flex-direction:column;gap:8px;font-size:11px;color:#888">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <b style="color:#aaa">jog / pre-prime</b>
    <span>rod <b id="jEpos" style="color:#8d8">0.000</b>mm &nbsp; <span id="jState" style="color:#a66">disconnected</span></span>
  </div>
  <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
    <button id="jConnect" onclick="jogConnect()" style="border-color:#46a;color:#88d">connect</button>
    <span>E step</span>
    <select id="jEstep" style="background:#1a1a1a;color:#ccc;border:1px solid #333;font-family:monospace">
      <option>0.05</option><option>0.1</option><option selected>0.2</option><option>0.4</option><option>0.8</option><option>1.5</option>
    </select>
    <button onclick="jog('e', estep())">&#9650; extrude</button>
    <button onclick="jog('e', -estep())">&#9660; retract</button>
    <button onclick="jog('prime', estep()*5)" style="border-color:#4a6;color:#8d8">PRIME 5&times;</button>
  </div>
  <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
    <span>Z step</span>
    <select id="jZstep" style="background:#1a1a1a;color:#ccc;border:1px solid #333;font-family:monospace">
      <option>0.05</option><option>0.1</option><option>0.5</option><option selected>1.0</option><option>5.0</option><option>10.0</option>
    </select>
    <button onclick="jog('z', zstep())">Z &#8593;</button>
    <button onclick="jog('z', -zstep())">Z &#8595;</button>
    <button onclick="jog('home')">home</button>
    <button onclick="jog('z0')">Z0</button>
    <button onclick="jog('disable')">motors off</button>
    <button onclick="jog('pos')">pos</button>
  </div>
  <div id="jMsg" style="min-height:14px;color:#666"></div>
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
    sensor_amp:    parseFloat(document.getElementById('pSAmp').value),
    line_width:   parseFloat(document.getElementById('pLine').value),
    layer_height: parseFloat(document.getElementById('pLayerH').value),
    total_layers: parseInt(document.getElementById('pLayers').value),
    base_layers:  parseInt(document.getElementById('pBase').value),
    max_overhang: parseFloat(document.getElementById('pOverhang').value) / 100,
    print_speed:  parseFloat(document.getElementById('pSpeed').value),
    flow_pct:     parseInt(document.getElementById('pFlow').value),
    nozzle_temp:  parseInt(document.getElementById('pNozzle').value),
    bed_temp:     parseInt(document.getElementById('pBed').value),
    wobble_amp:      parseFloat(document.getElementById('pWobble').value),
    spacing_min_mm:  parseFloat(document.getElementById('pSpacingMin').value),
    spacing_max_mm:  parseFloat(document.getElementById('pSpacingMax').value),
    spacing_sens:    parseFloat(document.getElementById('pSpacingSens').value),
    point_smooth_mm: parseFloat(document.getElementById('pPointSmooth').value),
    pitch_centre_hz: parseFloat(document.getElementById('pPitchCentre').value),
    pitch_range_hz:  parseFloat(document.getElementById('pPitchRange').value),
  };
}

function validate() {
  const lw     = parseFloat(document.getElementById('pLine').value);
  const lh     = parseFloat(document.getElementById('pLayerH').value);
  const spd    = parseFloat(document.getElementById('pSpeed').value);
  const nozzle = parseFloat(document.getElementById('pNozzleDia').value);
  const MAX_FLOW = 10.0;

  const flow = spd * lw * lh;
  const warnings = [];

  const red   = '#c44';
  const clear = '';

  const flowOver = flow > MAX_FLOW;
  const flowWarn = flow > MAX_FLOW * 0.8;
  document.getElementById('pSpeed').style.borderColor  = flowOver ? red : (flowWarn ? '#a84' : clear);
  document.getElementById('pLine').style.borderColor   = flowOver ? red : clear;
  document.getElementById('pLayerH').style.borderColor = flowOver ? red : clear;
  if (flowOver) warnings.push('volumetric flow ' + flow.toFixed(1) + ' mm³/s — max ~10 (reduce speed or layer height)');
  else if (flowWarn) warnings.push('flow ' + flow.toFixed(1) + ' mm³/s — approaching limit');

  if (lh > lw * 0.8) {
    document.getElementById('pLayerH').style.borderColor = red;
    warnings.push('layer height > 80% of line width — risk of poor bonding');
  }

  const flow_pct = parseInt(document.getElementById('pFlow').value);
  if (flow_pct > 200) {
    document.getElementById('pFlow').style.borderColor = '#a84';
    warnings.push('flow ' + flow_pct + '% — watch for over-extrusion');
  }

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
let freqPollInterval = null;
function startFreqPoll() {
  if (freqPollInterval) return;
  freqPollInterval = setInterval(function() {
    fetch('/freq').then(function(r){return r.json();}).then(function(d){
      if (d.hz > 0) document.getElementById('sFreq').textContent = Math.round(d.hz);
    }).catch(function(){});
    fetch('/voice').then(function(r){return r.json();}).then(function(d){
      if (d.voice  !== undefined) document.getElementById('sTemp').textContent = d.voice.toFixed(2);
      if (d.voiceX !== undefined) document.getElementById('sVoiceX').textContent = d.voiceX.toFixed(2);
      if (d.voiceY !== undefined) document.getElementById('sVoiceY').textContent = d.voiceY.toFixed(2);
      if (d.n_pts   !== undefined) document.getElementById('sNPoints').textContent = d.n_pts;
      if (d.spacing !== undefined) document.getElementById('sSpacing').textContent = d.spacing.toFixed(2);
    }).catch(function(){});
  }, 300);
}
function stopFreqPoll() {
  if (freqPollInterval) { clearInterval(freqPollInterval); freqPollInterval = null; }
}
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

function saveDefaults() {
  var ids = ['pSides','pDiam','pPoints','pNozzleDia','pLine','pLayerH',
             'pLayers','pBase','pOverhang','pSpeed','pFlow',
             'pNozzle','pBed','pSAmp','pWobble','pSpacingMin','pSpacingMax','pSpacingSens','pPointSmooth',
             'pPitchCentre','pPitchRange'];
  var d = {};
  ids.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) d[id] = el.value;
  });
  localStorage.setItem('gen3d_defaults', JSON.stringify(d));
  var btn = document.getElementById('btnSave');
  btn.textContent = 'saved!';
  setTimeout(function(){ btn.textContent = 'save as default'; }, 1500);
}
function loadDefaults() {
  var raw = localStorage.getItem('gen3d_defaults');
  if (!raw) return;
  var d = JSON.parse(raw);
  Object.keys(d).forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = d[id];
  });
}

// ── jog / pre-prime console ──────────────────────────────────────────────
let jogConnected = false;
function estep(){ return parseFloat(document.getElementById('jEstep').value); }
function zstep(){ return parseFloat(document.getElementById('jZstep').value); }
function setJogUI(conn){
  jogConnected = conn;
  var st = document.getElementById('jState');
  st.textContent = conn ? 'connected' : 'disconnected';
  st.style.color = conn ? '#6a6' : '#a66';
  document.getElementById('jConnect').textContent = conn ? 'disconnect' : 'connect';
}
function jogMsg(m){ document.getElementById('jMsg').textContent = m || ''; }

function estop(){
  // no confirm — an e-stop must be instant
  fetch('/estop', {method:'POST'}).then(r=>r.json()).then(d=>{
    jogMsg(d.msg || 'E-STOP');
  }).catch(()=>{});
  if (es) { es.close(); es = null; }
  document.getElementById('sLayer').textContent = 'E-STOP';
  stopAll(false);
  setJogUI(false);
}
function jogConnect(){
  var act = jogConnected ? 'disconnect' : 'connect';
  jogMsg(jogConnected ? 'disconnecting…' : 'connecting… (board resets, ~12s)');
  fetch('/jog?action=' + act, {method:'POST'}).then(r=>r.json()).then(d=>{
    setJogUI(!!d.connected);
    if (d.e_pos !== undefined) document.getElementById('jEpos').textContent = d.e_pos.toFixed(3);
    jogMsg(d.msg);
  }).catch(e=>jogMsg('error: ' + e));
}
function jog(action, mm){
  var url = '/jog?action=' + action;
  if (mm !== undefined) url += '&mm=' + mm;
  fetch(url, {method:'POST'}).then(r=>r.json()).then(d=>{
    if (d.connected === false) setJogUI(false);
    if (d.e_pos !== undefined) document.getElementById('jEpos').textContent = d.e_pos.toFixed(3);
    jogMsg(d.ok ? d.msg : ('⚠ ' + (d.msg || 'failed')));
  }).catch(e=>jogMsg('error: ' + e));
}
fetch('/jog_status').then(r=>r.json()).then(d=>{
  setJogUI(!!d.connected);
  if (d.e_pos !== undefined) document.getElementById('jEpos').textContent = d.e_pos.toFixed(3);
}).catch(()=>{});

loadDefaults();
validate();

function toScreen(x, y) {
  return [CX + (x - 175) * SCALE, CY - (y - 175) * SCALE];
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
  if (d.freq_hz !== undefined && d.freq_hz > 0) document.getElementById('sFreq').textContent = Math.round(d.freq_hz);
  if (d.temp !== undefined) {
    document.getElementById('sTemp').textContent = d.temp.toFixed(2);
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
  document.getElementById('btnRecon').style.display = 'none';
  document.getElementById('btnStop').disabled = true;
  stopTempPoll();
  stopFreqPoll();
}

function openStream(url) {
  if (es) { es.close(); es = null; }
  ctx.clearRect(0, 0, W, H);
  layerCount = 0; lastPt = null;
  SCALE = 200 / (parseFloat(document.getElementById('pDiam').value) / 2 + 12);
  document.getElementById('btnViz').disabled = true;
  document.getElementById('btnPrint').disabled = true;
  document.getElementById('btnRecon').style.display = 'none';
  document.getElementById('btnStop').disabled = false;
  document.getElementById('sLayer').textContent = '—';
  document.getElementById('sEta').textContent = '—';

  es = new EventSource(url);
  es.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.keepalive) return;
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
  es.onerror = function() {
    // Don't call stopAll — just show reconnect button so user can rejoin
    document.getElementById('btnRecon').style.display = '';
  };
}

function startViz()   { openStream('/viz_stream?' + new URLSearchParams(params())); }

function startPrint() {
  fetch('/status').then(r => r.json()).then(d => {
    if (d.printing) {
      // Already running — just reconnect the stream
      startTempPoll(); startFreqPoll();
      openStream('/print_stream?' + new URLSearchParams(params()));
    } else {
      if (!confirm('Start print? Confirm: clay primed (nozzle gushing), floor clear.')) return;
      setJogUI(false);  // server closes the jog connection to take the port
      startTempPoll(); startFreqPoll();
      openStream('/print_stream?' + new URLSearchParams(params()));
    }
  }).catch(() => {
    if (!confirm('Start print? Confirm: clay primed (nozzle gushing), floor clear.')) return;
    setJogUI(false);
    startTempPoll(); startFreqPoll();
    openStream('/print_stream?' + new URLSearchParams(params()));
  });
}

function reconnect() {
  startTempPoll(); startFreqPoll();
  openStream('/print_stream?' + new URLSearchParams(params()));
}

startTempPoll();
startFreqPoll();

// Auto-reconnect on page load if a print is already running on the Pi
fetch('/status').then(r => r.json()).then(d => {
  if (d.printing) {
    document.getElementById('sLayer').textContent = '(reconnecting…)';
    startTempPoll(); startFreqPoll();
    openStream('/print_stream?' + new URLSearchParams(params()));
  }
}).catch(() => {});
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
    p['sensor_source'] = 'sound'  # cpu mode retired -- always vibration sensor
    p['line_width']   = float(args.get('line_width', p['line_width']))
    p['layer_height'] = float(args.get('layer_height', p['layer_height']))
    p['total_layers'] = int(args.get('total_layers', p['total_layers']))
    p['base_layers']  = int(args.get('base_layers',  p['base_layers']))
    p['max_overhang'] = float(args.get('max_overhang', p['max_overhang']))
    p['print_speed']  = float(args.get('print_speed', p['print_speed']))
    p['flow_pct']     = int(args.get('flow_pct',     p.get('flow_pct', 100)))
    p['nozzle_temp']  = int(args.get('nozzle_temp',  p['nozzle_temp']))
    p['bed_temp']     = int(args.get('bed_temp',     p['bed_temp']))
    p['wobble_amp']    = float(args.get('wobble_amp',    p.get('wobble_amp', 0.0)))
    p['spacing_min_mm'] = float(args.get('spacing_min_mm', p.get('spacing_min_mm', 3.0)))
    p['spacing_max_mm'] = float(args.get('spacing_max_mm', p.get('spacing_max_mm', 5.0)))
    p['spacing_sens']   = float(args.get('spacing_sens',   p.get('spacing_sens',  20.0)))
    p['point_smooth_mm']  = float(args.get('point_smooth_mm',  p.get('point_smooth_mm',  2.0)))
    p['pitch_centre_hz']  = float(args.get('pitch_centre_hz',  p.get('pitch_centre_hz',  500.0)))
    p['pitch_range_hz']   = float(args.get('pitch_range_hz',   p.get('pitch_range_hz',   400.0)))
    return p


def _start_print_thread(p, dry_run=False):
    """Start run_print in a daemon thread; events go to all _broadcast_qs subscribers."""

    def on_layer_combined(layer, pts, z, samples, temp, is_base, radius_delta, eta_s):
        try:
            import main as _main
            freq_hz = _main._current_freq_hz[0]
        except Exception:
            freq_hz = 0.0
        _broadcast({'type': 'layer_start', 'layer': layer, 'z': round(z, 3),
                    'temp': round(temp, 3), 'freq_hz': round(freq_hz, 1), 'is_base': is_base})
        _broadcast({'type': 'layer_end', 'layer': layer, 'z': round(z, 3),
                    'temp': round(temp, 3), 'freq_hz': round(freq_hz, 1),
                    'radius_delta': round(radius_delta, 4),
                    'eta_s': round(eta_s, 1), 'is_base': is_base})

    def on_point(x, y):
        _broadcast({'type': 'point', 'x': round(x, 3), 'y': round(y, 3)})

    def do_run():
        _print_active.set()
        # release any idle jog connection so run_print can own the serial port
        # (only for a real print — a dry-run preview never touches the port)
        if not dry_run:
            with _jog_lock:
                if _jog_printer[0]:
                    try:
                        _jog_printer[0].close()
                    except Exception:
                        pass
                    _jog_printer[0] = None
        try:
            from main import run_print
            run_print(p=p, dry_run=dry_run, on_layer=on_layer_combined,
                      on_point=on_point, sensor_fn=None, stop_flag=_stop_flag)
        except Exception as ex:
            _broadcast({'error': str(ex)})
        finally:
            _print_active.clear()
            _broadcast(None)  # signal done to every subscriber

    t = threading.Thread(target=do_run, daemon=True)
    t.start()


def _sse_from_queue(q):
    """Generator: read from per-client queue, yield SSE lines, unsubscribe on exit."""
    try:
        while True:
            try:
                item = q.get(timeout=30)
            except Exception:
                yield 'data: {"keepalive":true}\n\n'
                continue
            if item is None:
                yield 'data: {"done":true}\n\n'
                break
            yield 'data: ' + json.dumps(item) + '\n\n'
    finally:
        _unsub(q)


@app.route('/')
def index():
    return HTML.replace('{VERSION}', VERSION)


@app.route('/stop', methods=['POST'])
def stop():
    _stop_flag.set()
    return ('', 204)


@app.route('/status')
def status():
    return json.dumps({'printing': _print_active.is_set()})


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
        if _printer_ref[0]:
            try:
                _printer_ref[0].send(f'M221 S{pct}')
            except:
                pass
    except ValueError:
        pass
    return ('', 204)


@app.route('/voice')
def voice_data():
    try:
        import main as _main
        return json.dumps({
            'voice':  round(_main._current_voice_mm[0], 4),
            'voiceX': round(_main._current_voice_x[0], 4),
            'voiceY': round(_main._current_voice_y[0], 4),
            'n_pts':   _main._current_n_points[0],
            'spacing': round(_main._current_spacing_mm[0], 2),
        })
    except Exception as e:
        return json.dumps({'error': str(e)})


@app.route('/freq')
def freq_data():
    try:
        import main as _main
        return json.dumps({'hz': round(_main._current_freq_hz[0], 1)})
    except Exception:
        return json.dumps({'hz': 0})


@app.route('/viz_stream')
def viz_stream():
    p = parse_params(request.args)
    q = _sub()
    if not _print_active.is_set():
        _stop_flag.clear()
        _last_layer_evt[0] = None
        _start_print_thread(p, dry_run=True)
    elif _last_layer_evt[0]:
        q.put(_last_layer_evt[0])
    return Response(_sse_from_queue(q), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/print_stream')
def print_stream():
    p = parse_params(request.args)
    q = _sub()
    if not _print_active.is_set():
        _stop_flag.clear()
        _last_layer_evt[0] = None
        _start_print_thread(p, dry_run=False)
    elif _last_layer_evt[0]:
        q.put(_last_layer_evt[0])
    return Response(_sse_from_queue(q), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/estop', methods=['POST'])
def estop():
    """EMERGENCY STOP: halt host streaming NOW and fire M112. Covers whichever
    serial connection viz_app owns -- the live print AND/OR the idle jog. Closing
    the port is the decisive action (stops G1 streaming instantly regardless of
    firmware EMERGENCY_PARSER); M112 hard-kills too if the parser is enabled.
    NOTE: cannot reach a standalone jog.py or any other process's port.
    The physical power switch remains the ultimate stop."""
    _stop_flag.set()
    with _cmd_lock:
        del _cmd_queue[:]
    acted = []
    for name, ref in (('print', _printer_ref), ('jog', _jog_printer)):
        pr = ref[0]
        if pr is None:
            continue
        try:
            pr.s.write(b'\r\nM112\r\n')   # fire straight to the wire, no ok-wait
            pr.s.flush()
            acted.append(name + ':M112')
        except Exception as e:
            acted.append(name + ':werr')
        try:
            pr.s.close()                  # stop host streaming instantly
        except Exception:
            pass
        ref[0] = None
    _jog_e_pos[0] = 0.0
    msg = 'E-STOP fired (' + ', '.join(acted) + ')' if acted else 'E-STOP: no live connection owned by web UI (flag set)'
    return json.dumps({'ok': True, 'msg': msg})


@app.route('/jog_status')
def jog_status():
    return json.dumps({'connected': _jog_printer[0] is not None,
                       'e_pos': round(_jog_e_pos[0], 3),
                       'printing': _print_active.is_set()})


@app.route('/jog', methods=['POST'])
def jog():
    """Idle jog / pre-prime console. action = connect|disconnect|e|z|prime|home|
    z0|enable|disable|pos ; mm = signed float for e/z/prime. Ceramic-safe:
    E moves at F6 (<= the F12 AVR-crash ceiling), never retract-fast."""
    action = request.args.get('action', '')
    if _print_active.is_set():
        return json.dumps({'ok': False, 'msg': 'print running — jog disabled', 'connected': False})
    with _jog_lock:
        try:
            if action == 'connect':
                if _jog_printer[0] is None:
                    from printer import Printer
                    from main import PORT
                    pr = Printer(PORT, boot_wait=12.0)
                    for c in ['M999', 'T0', 'M302 P1', 'G91', 'M17']:  # cold-extrude, relative, motors on
                        pr.send(c)
                    _jog_printer[0] = pr
                    _jog_e_pos[0] = 0.0
                return json.dumps({'ok': True, 'msg': 'connected', 'connected': True, 'e_pos': _jog_e_pos[0]})

            if action == 'disconnect':
                if _jog_printer[0]:
                    try:
                        _jog_printer[0].close()
                    except Exception:
                        pass
                    _jog_printer[0] = None
                return json.dumps({'ok': True, 'msg': 'disconnected', 'connected': False})

            pr = _jog_printer[0]
            if pr is None:
                return json.dumps({'ok': False, 'msg': 'not connected', 'connected': False})

            if action in ('e', 'prime'):
                d = float(request.args.get('mm', '0'))
                pr.send('G1 E{:.3f} F6'.format(d))
                _jog_e_pos[0] += d
                return json.dumps({'ok': True, 'msg': 'E {:+.3f}'.format(d),
                                   'e_pos': round(_jog_e_pos[0], 3), 'connected': True})
            if action == 'z':
                d = float(request.args.get('mm', '0'))
                pr.send('G1 Z{:.3f} F300'.format(d))
                return json.dumps({'ok': True, 'msg': 'Z {:+.3f}'.format(d), 'connected': True})
            if action == 'home':
                for c in ['G90', 'G28', 'G91']:
                    pr.send(c)
                _jog_e_pos[0] = 0.0
                return json.dumps({'ok': True, 'msg': 'homed', 'e_pos': 0.0, 'connected': True})
            if action == 'z0':
                for c in ['G90', 'G1 Z0 F300', 'G91']:
                    pr.send(c)
                return json.dumps({'ok': True, 'msg': 'at Z0', 'connected': True})
            if action == 'enable':
                pr.send('M17')
                return json.dumps({'ok': True, 'msg': 'motors on', 'connected': True})
            if action == 'disable':
                pr.send('M84')
                return json.dumps({'ok': True, 'msg': 'motors off', 'connected': True})
            if action == 'pos':
                pr.s.reset_input_buffer()
                pr.s.write(b'M114\n')
                line = ''
                t0 = time.time()
                while time.time() - t0 < 3:
                    l = pr.s.readline().decode(errors='replace').strip()
                    if l.startswith('X:'):
                        line = l
                        break
                return json.dumps({'ok': True, 'msg': line or '?', 'connected': True})

            return json.dumps({'ok': False, 'msg': 'unknown action: ' + action, 'connected': True})
        except Exception as ex:
            return json.dumps({'ok': False, 'msg': str(ex), 'connected': _jog_printer[0] is not None})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
