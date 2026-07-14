/*
 * PROJECT: gen3d
 * DEVICE:  Printer sound sensor — INMP441 I2S digital microphone + FFT pitch
 *
 * Streams "freq_hz,amplitude" CSV at ~30 Hz over USB serial.
 * - amplitude: EMA-smoothed normalised RMS (0.0–1.0 before GAIN)
 * - freq_hz:   dominant frequency from FFT (Hz), EMA-smoothed
 *
 * Pi reads each line; comma triggers the multi-field parser:
 *   _current_freq_hz  ← p[0]   (lights up freq display in viz)
 *   _current_voice_mm ← p[1] * amp_mm  (drives radius variation)
 *
 * REQUIRES: arduinoFFT library — install via Arduino IDE Library Manager:
 *   Sketch → Include Library → Manage Libraries → search "arduinoFFT" → install
 *
 * CONFIGURATION:
 *   GAIN          — raise if amplitude values stay small
 *   SMOOTH_ALPHA  — EMA on amplitude (lower = smoother/slower)
 *   FREQ_ALPHA    — EMA on frequency (lower = smoother pitch tracking)
 *   FREQ_MIN_HZ   — ignore FFT bins below this (cuts motor DC hum)
 *   FREQ_MAX_HZ   — ignore FFT bins above this (cuts high noise)
 *
 * WIRING:
 *   INMP441   ESP32-S3 SuperMini
 *   GND    →  GND
 *   VCC    →  3V3
 *   SCK    →  GPIO 11
 *   WS     →  GPIO 12
 *   SD     →  GPIO 13
 *   L/R    →  GND   (left channel)
 *
 * FLASH SETTINGS (Arduino IDE):
 *   Board              : ESP32S3 Dev Module
 *   USB CDC On Boot    : Enabled
 *   Flash Size         : 4MB (32Mb)
 *   Partition Scheme   : Default 4MB with spiffs
 *   Upload Mode        : UART0 / Hardware CDC
 *   Upload Speed       : 921600
 *   Port               : reselect after flash — COM number changes
 */

#define VERSION "1.2"

// ── pins ──────────────────────────────────────────────────────────────────
#define PIN_SCK  11
#define PIN_WS   12
#define PIN_SD   13

// ── audio config ──────────────────────────────────────────────────────────
#define SAMPLE_RATE     16000
#define BUFFER_SAMPLES  512     // 512/16000 = 32 ms per read → ~31 Hz output
#define SMOOTH_ALPHA    0.2f    // EMA on amplitude
#define FREQ_ALPHA      0.3f    // EMA on frequency (higher = more responsive)
#define GAIN            10.0f

// ── FFT config ────────────────────────────────────────────────────────────
#define FREQ_MIN_HZ     80      // ignore below (cuts DC / motor rumble)
#define FREQ_MAX_HZ     6000    // ignore above (cuts high-frequency noise)

// ── I2S driver ────────────────────────────────────────────────────────────
#include <driver/i2s.h>
#include <arduinoFFT.h>

static float   g_smoothed_amp  = 0.0f;
static float   g_smoothed_freq = 0.0f;
static int32_t g_buf[BUFFER_SAMPLES];

// FFT working buffers (double precision required by arduinoFFT)
static double vReal[BUFFER_SAMPLES];
static double vImag[BUFFER_SAMPLES];

ArduinoFFT<double> FFT = ArduinoFFT<double>(vReal, vImag, BUFFER_SAMPLES, (double)SAMPLE_RATE);

void setupI2S() {
  i2s_config_t cfg = {
    .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate          = SAMPLE_RATE,
    .bits_per_sample      = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count        = 4,
    .dma_buf_len          = 256,
    .use_apll             = false,
    .tx_desc_auto_clear   = false,
    .fixed_mclk           = 0,
  };
  i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);

  i2s_pin_config_t pins = {
    .mck_io_num   = I2S_PIN_NO_CHANGE,
    .bck_io_num   = PIN_SCK,
    .ws_io_num    = PIN_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num  = PIN_SD,
  };
  i2s_set_pin(I2S_NUM_0, &pins);
  i2s_zero_dma_buffer(I2S_NUM_0);
}

// Read one buffer from I2S. Computes RMS and fills vReal/vImag for FFT.
// Returns normalised RMS (0.0–1.0 before GAIN).
float readAndPrepare() {
  size_t bytes_read = 0;
  i2s_read(I2S_NUM_0, g_buf, sizeof(g_buf), &bytes_read, portMAX_DELAY);

  int n = bytes_read / sizeof(int32_t);
  if (n == 0) return 0.0f;

  double sum_sq = 0.0;
  for (int i = 0; i < n; i++) {
    int32_t s = g_buf[i] >> 8;   // 24-bit signed sample
    double  d = (double)s;
    sum_sq   += d * d;
    vReal[i]  = d;
    vImag[i]  = 0.0;
  }
  // zero-pad if buffer shorter than FFT size
  for (int i = n; i < BUFFER_SAMPLES; i++) { vReal[i] = 0.0; vImag[i] = 0.0; }

  float rms = (float)sqrt(sum_sq / n);
  return rms / 8388608.0f;   // normalise: 2^23
}

// FFT peak detection over the printer-relevant frequency range.
float computePeakFreq() {
  FFT.windowing(FFTWindow::Hann, FFTDirection::Forward);
  FFT.compute(FFTDirection::Forward);
  FFT.complexToMagnitude();

  const int minBin = (int)((double)FREQ_MIN_HZ * BUFFER_SAMPLES / SAMPLE_RATE) + 1;
  const int maxBin = (int)((double)FREQ_MAX_HZ * BUFFER_SAMPLES / SAMPLE_RATE);
  const int nyquist = BUFFER_SAMPLES / 2;
  const int clampedMax = min(maxBin, nyquist - 1);

  double peakMag = 0.0;
  int    peakBin = minBin;
  for (int i = minBin; i <= clampedMax; i++) {
    if (vReal[i] > peakMag) {
      peakMag = vReal[i];
      peakBin = i;
    }
  }
  return (float)peakBin * (float)SAMPLE_RATE / (float)BUFFER_SAMPLES;
}

void setup() {
  Serial.begin(115200);
  setupI2S();
  // discard first two buffers — INMP441 needs a moment to settle
  size_t dummy;
  i2s_read(I2S_NUM_0, g_buf, sizeof(g_buf), &dummy, portMAX_DELAY);
  i2s_read(I2S_NUM_0, g_buf, sizeof(g_buf), &dummy, portMAX_DELAY);
}

void loop() {
  float raw_amp  = readAndPrepare() * GAIN;   // also fills vReal/vImag
  float raw_freq = computePeakFreq();          // uses vReal/vImag in-place

  g_smoothed_amp  = SMOOTH_ALPHA * raw_amp  + (1.0f - SMOOTH_ALPHA) * g_smoothed_amp;
  g_smoothed_freq = FREQ_ALPHA   * raw_freq + (1.0f - FREQ_ALPHA)   * g_smoothed_freq;

  // "freq_hz,amplitude" — Pi comma-parser routes these to freq display + radius
  Serial.print(g_smoothed_freq, 1);
  Serial.print(',');
  Serial.println(g_smoothed_amp, 4);
}
