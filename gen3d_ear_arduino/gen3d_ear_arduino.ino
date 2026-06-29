/*
 * PROJECT: gen3d / Birch as Cybernetic System
 * DEVICE:  Sound-sense node -- the printer's voice becomes the sculptor's hand
 *
 * Samples the KY-038 microphone module at ~8kHz, estimates dominant
 * frequency via zero-crossing analysis. Low frequencies (slow/grinding
 * stepper) map to negative offset (wall contracts); high frequencies
 * (fast/whining stepper) map to positive (wall expands). Silence
 * produces no movement -- the printer must speak to be heard.
 * Streams a float (-1.0 to +1.0) over USB serial every 100ms.
 *
 * WIRING:
 *   KY-038 VCC -> 3V3
 *   KY-038 GND -> GND
 *   KY-038 AO  -> GPIO1 (ADC1_CH0)
 *   KY-038 DO  -> not connected
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

// -- config --
#define ADC_PIN        7      // GPIO1 = ADC1_CH0
#define SAMPLE_COUNT   256    // samples per window
#define SAMPLE_US      125    // ~8kHz
#define FREQ_CENTRE    1700  // Hz -- maps to zero offset (stepper range ~800-3000Hz)
#define FREQ_RANGE     1500   // Hz -- +/- range
#define AMP_FLOOR      1     // ADC units -- noise floor
#define AMP_SCALE      0.5   // rms deviation for full +/-1 output swing
#define SEND_INTERVAL  100    // ms between serial outputs

int samples[SAMPLE_COUNT];
float g_freqHz = 0.0f;
unsigned long lastSend = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);
  analogSetPinAttenuation(ADC_PIN, ADC_11db);
  delay(500);
}

float listenToMachine() {
  // collect samples
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    samples[i] = analogRead(ADC_PIN);
    delayMicroseconds(SAMPLE_US);
  }

  // mean
  long sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) sum += samples[i];
  float mean = (float)sum / SAMPLE_COUNT;

  // RMS amplitude
  float variance = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    float d = samples[i] - mean;
    variance += d * d;
  }
  float rms = sqrt(variance / SAMPLE_COUNT);

 //Serial.print("rms:"); Serial.println(rms);  // <-- ADD THIS LINE 

  // silence -- printer is still
  if (rms < AMP_FLOOR) return 0.0f;

  // zero-crossing count -> frequency
  int crossings = 0;
  for (int i = 1; i < SAMPLE_COUNT; i++) {
    if ((samples[i-1] - mean) * (samples[i] - mean) < 0) crossings++;
  }
  float windowSec = (float)(SAMPLE_COUNT * SAMPLE_US) / 1000000.0f;
  float freqHz = (crossings / 2.0f) / windowSec;

  g_freqHz = freqHz;
  // normalise frequency around FREQ_CENTRE
  float freqNorm = (freqHz - FREQ_CENTRE) / (float)FREQ_RANGE;
  freqNorm = max(-1.0f, min(1.0f, freqNorm));

  // scale by amplitude
  // EMA auto-calibrates centre to ambient -- no manual tuning needed
  static float avg_rms = -1.0f;
  if (avg_rms < 0) avg_rms = rms;
  avg_rms = avg_rms * 0.95f + rms * 0.05f;  // ~20-sample window
  float voice = (rms - avg_rms) / (float)AMP_SCALE;  // +/- around mean
  return max(-1.0f, min(1.0f, voice));
}
void loop() {
  float voice = listenToMachine();

  unsigned long now = millis();
  if (now - lastSend >= SEND_INTERVAL) {
    Serial.print(g_freqHz, 1);
    Serial.print(',');
    Serial.println(voice, 4);
    lastSend = now;
  }
}