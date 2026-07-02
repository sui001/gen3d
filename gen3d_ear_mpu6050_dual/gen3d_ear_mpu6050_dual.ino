/*
 * PROJECT: gen3d / Birch as Cybernetic System
 * DEVICE:  Dual vibration-sense node -- X-motor and Y-motor frame vibration
 *
 * Two MPU-6050 boards, one near the X-axis motor, one near the Y-axis motor.
 * As the head traces a circular path, X-velocity and Y-velocity are
 * sinusoidal but 90 degrees out of phase -- so the two sensors naturally
 * peak at different points around the revolution. The difference between
 * them gives a signal tied to the actual angle of travel, not a synthetic
 * wobble -- a genuine physical correlate of where the head is.
 *
 * Each sensor's vibration RMS is EMA-centred to its own ambient level (same
 * auto-calibration trick as the single-sensor version), then the two are
 * combined: voice = clamp(voiceX - voiceY, -1, 1).
 *
 * Streams "freqHz,voice,voiceX,voiceY" CSV every 100ms. main.py only reads
 * the first two fields, so the Pi side needs no changes. voiceX/voiceY are
 * included for debugging/tuning in the Serial Monitor.
 *
 * WIRING (shared I2C bus, different addresses via AD0):
 *   Sensor 1 (near X motor):  VCC->3V3  GND->GND  SCL->GPIO9  SDA->GPIO8  AD0->GND   (addr 0x68)
 *   Sensor 2 (near Y motor):  VCC->3V3  GND->GND  SCL->GPIO9  SDA->GPIO8  AD0->3V3   (addr 0x69)
 *   XCL / XDA / INT on both -- not connected
 *
 * MOUNTING: zip-tie/bolt to the frame as close to each motor as practical.
 * Keep leads long enough to reach but not so long they flop around.
 *
 * ARDUINO IDE SETTINGS:
 *   Board:            ESP32S3 Dev Module
 *   USB CDC On Boot:  Enabled
 *   Flash Size:       4MB (32Mb)
 *   Partition Scheme: Default 4MB with spiffs
 *   Upload Mode:      UART0 / Hardware CDC
 *   Upload Speed:     921600
 */

#define VERSION       "1.5"

#include <Wire.h>

// -- config --
#define SDA_PIN        8
#define SCL_PIN        9
#define ADDR_X         0x68   // AD0 -> GND
#define ADDR_Y         0x69   // AD0 -> 3V3

#define SAMPLE_COUNT   128    // samples per window
#define SAMPLE_US      1000   // ~1kHz per sensor
#define AMP_SCALE      0.02   // rms-g deviation for full +/-1 swing, per sensor
#define COMBINE_SCALE  1.0    // scales (voiceX - voiceY) before clamping
#define SEND_INTERVAL  100    // ms between serial outputs

float samplesX[SAMPLE_COUNT];
float samplesY[SAMPLE_COUNT];
float g_freqHz = 0.0f;
float g_avgX = -1.0f;
float g_avgY = -1.0f;
unsigned long lastSend = 0;

void mpuWake(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0x6B);  // PWR_MGMT_1
  Wire.write(0x00);  // wake up, internal 8MHz clock
  Wire.endTransmission();
}

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  mpuWake(ADDR_X);
  mpuWake(ADDR_Y);
  delay(100);
}

// Reads accel XYZ from given I2C address, returns magnitude in g
float readAccelMagnitude(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0x3B);  // ACCEL_XOUT_H
  Wire.endTransmission(false);
  Wire.requestFrom((int)addr, 6, true);

  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();

  float fx = ax / 16384.0f;  // +/-2g range -> 16384 LSB/g
  float fy = ay / 16384.0f;
  float fz = az / 16384.0f;

  return sqrtf(fx * fx + fy * fy + fz * fz);
}

// Computes EMA-centred vibration voice (-1..+1) for one sensor's sample buffer
float voiceFromSamples(float *samples, float &avg_rms_state) {
  float sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) sum += samples[i];
  float mean = sum / SAMPLE_COUNT;

  float variance = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    float d = samples[i] - mean;
    variance += d * d;
  }
  float rms = sqrtf(variance / SAMPLE_COUNT);

  if (avg_rms_state < 0) avg_rms_state = rms;
  // continuous slow EMA -- ~60-70s window, never freezes so it can self-correct
  // over several layers if the early baseline happened to be unrepresentative
  avg_rms_state = avg_rms_state * 0.9985f + rms * 0.0015f;

  float v = (rms - avg_rms_state) / (float)AMP_SCALE;
  return max(-1.0f, min(1.0f, v));
}

// Rough combined vibration frequency (display only) from the X sensor
float freqFromSamples(float *samples) {
  float sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) sum += samples[i];
  float mean = sum / SAMPLE_COUNT;

  int crossings = 0;
  for (int i = 1; i < SAMPLE_COUNT; i++) {
    if ((samples[i-1] - mean) * (samples[i] - mean) < 0) crossings++;
  }
  float windowSec = (float)(SAMPLE_COUNT * SAMPLE_US) / 1000000.0f;
  return (crossings / 2.0f) / windowSec;
}

void listenToMachine(float &voiceOut, float &voiceXOut, float &voiceYOut) {
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    samplesX[i] = readAccelMagnitude(ADDR_X);
    samplesY[i] = readAccelMagnitude(ADDR_Y);
    delayMicroseconds(SAMPLE_US);
  }

  voiceXOut = voiceFromSamples(samplesX, g_avgX);
  voiceYOut = voiceFromSamples(samplesY, g_avgY);

  g_freqHz = freqFromSamples(samplesX);

  voiceOut = max(-1.0f, min(1.0f, (voiceXOut - voiceYOut) * (float)COMBINE_SCALE));
}

void checkResetCommand() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'R') {
      g_avgX = -1.0f;  // forces reseed on next sample -- fresh baseline this layer
      g_avgY = -1.0f;
    }
  }
}

void loop() {
  checkResetCommand();
  float voice, voiceX, voiceY;
  listenToMachine(voice, voiceX, voiceY);

  unsigned long now = millis();
  if (now - lastSend >= SEND_INTERVAL) {
    Serial.print(g_freqHz, 1);
    Serial.print(',');
    Serial.print(voice, 4);
    Serial.print(',');
    Serial.print(voiceX, 4);
    Serial.print(',');
    Serial.println(voiceY, 4);
    lastSend = now;
  }
}