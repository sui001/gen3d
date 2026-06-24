# gen3d_ear_v1.py
#
# PROJECT: gen3d / Birch as Cybernetic System
# DEVICE:  Sound-sense node -- the printer's voice becomes the sculptor's hand
#
# Listens to the KY-038 microphone module, estimating dominant frequency
# via zero-crossing analysis over a short sample window. Low frequencies
# (slow, grinding stepper movement) map to negative offset (wall contracts);
# high frequencies (fast, whining stepper) map to positive (wall expands).
# Silence produces no movement -- the printer must speak to be heard.
# Streams a normalised float (-1.0 to +1.0) to the Pi over USB serial
# every SEND_INTERVAL ms.
#
# WIRING:
#   KY-038 VCC -> 3V3
#   KY-038 GND -> GND
#   KY-038 AO  -> GPIO1  (ADC1_CH0 on S3 SuperMini)
#   KY-038 DO  -> not connected
#
# CONFIGURATION -- tune these to your printer's voice:
ADC_PIN       = 1      # GPIO1 = ADC1_CH0
SAMPLE_COUNT  = 256    # samples per window
SAMPLE_US     = 125    # ~8kHz sample rate (256 samples = ~32ms window)
FREQ_CENTRE   = 800    # Hz -- frequency that maps to zero (neither expand nor contract)
FREQ_RANGE    = 1500   # Hz -- +/- range; outside this clips to +/-1
AMP_FLOOR     = 80     # ADC units -- noise floor, below this = silence, no movement
AMP_SCALE     = 500    # ADC units -- amplitude for full-scale output
SEND_INTERVAL = 100    # ms between serial output lines

from machine import ADC, Pin
import time

VERSION = '1.0'

adc = ADC(Pin(ADC_PIN), atten=ADC.ATTN_11DB)  # full 0-3.3V range

def listenToMachine():
    """
    Sample the microphone, return a signed float representing the
    character of the sound: negative = low/slow, positive = high/fast.
    Returns 0.0 if the printer is silent.
    """
    samples = []
    for _ in range(SAMPLE_COUNT):
        samples.append(adc.read())
        time.sleep_us(SAMPLE_US)

    mean = sum(samples) / SAMPLE_COUNT

    # RMS amplitude -- how loud is the printer right now?
    rms = (sum((s - mean) ** 2 for s in samples) / SAMPLE_COUNT) ** 0.5

    if rms < AMP_FLOOR:
        return 0.0  # silence -- the printer holds still

    # Zero-crossing count -> frequency estimate
    crossings = sum(
        1 for i in range(1, SAMPLE_COUNT)
        if (samples[i - 1] - mean) * (samples[i] - mean) < 0
    )
    window_s = (SAMPLE_COUNT * SAMPLE_US) / 1_000_000
    freq_hz = (crossings / 2) / window_s

    # Normalise frequency: FREQ_CENTRE = 0, +/-FREQ_RANGE = +/-1
    freq_norm = max(-1.0, min(1.0, (freq_hz - FREQ_CENTRE) / FREQ_RANGE))

    # Scale by amplitude so quiet sounds produce subtler movement
    amp_norm = min(1.0, (rms - AMP_FLOOR) / AMP_SCALE)

    return freq_norm * amp_norm


last_send = time.ticks_ms()
while True:
    voice = listenToMachine()
    now = time.ticks_ms()
    if time.ticks_diff(now, last_send) >= SEND_INTERVAL:
        print(f'{voice:.4f}')
        last_send = now
