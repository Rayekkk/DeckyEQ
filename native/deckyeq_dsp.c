// SPDX-License-Identifier: BSD-3-Clause
// DeckyEQ's stereo stage for PipeWire's filter-chain: stereo width, a volume boost, then a
// look-ahead peak limiter. A LADSPA plugin, so it needs nothing beyond libc and libm.
//
// The limiter delays the audio by LOOKAHEAD_S. For every input frame it computes the gain that
// keeps that frame under the ceiling, takes the minimum over the look-ahead window, lets the gain
// recover with the release time, and smooths it with a moving average as long as the window.
// The average of a window that only holds gains at or below a peak's own gain cannot exceed it,
// so the peak leaves the delay line under the ceiling, reached by a ramp instead of a click.
#include <math.h>
#include <stdlib.h>
#include <string.h>

// The LADSPA 1.1 ABI, declared here rather than vendoring its header.
typedef float LADSPA_Data;
typedef void *LADSPA_Handle;
typedef int LADSPA_PortDescriptor;
typedef int LADSPA_PortRangeHintDescriptor;
typedef struct { LADSPA_PortRangeHintDescriptor HintDescriptor; LADSPA_Data LowerBound, UpperBound; } LADSPA_PortRangeHint;
typedef struct LADSPA_Descriptor {
  unsigned long UniqueID;
  const char *Label;
  int Properties;
  const char *Name, *Maker, *Copyright;
  unsigned long PortCount;
  const LADSPA_PortDescriptor *PortDescriptors;
  const char *const *PortNames;
  const LADSPA_PortRangeHint *PortRangeHints;
  void *ImplementationData;
  LADSPA_Handle (*instantiate)(const struct LADSPA_Descriptor *, unsigned long);
  void (*connect_port)(LADSPA_Handle, unsigned long, LADSPA_Data *);
  void (*activate)(LADSPA_Handle);
  void (*run)(LADSPA_Handle, unsigned long);
  void (*run_adding)(LADSPA_Handle, unsigned long);
  void (*set_run_adding_gain)(LADSPA_Handle, LADSPA_Data);
  void (*deactivate)(LADSPA_Handle);
  void (*cleanup)(LADSPA_Handle);
} LADSPA_Descriptor;

enum { IN = 1, OUT = 2, CONTROL = 4, AUDIO = 8 };
enum { BOUNDED_BELOW = 1, BOUNDED_ABOVE = 2, DEFAULT_MIDDLE = 0xC0, DEFAULT_MAXIMUM = 0x140,
       DEFAULT_0 = 0x200, DEFAULT_1 = 0x240, DEFAULT_100 = 0x280 };
enum { IN_L, IN_R, OUT_L, OUT_R, WIDTH, LIMIT, CEILING, RELEASE, GAIN, REDUCTION, LATENCY, PORTS };

#define LOOKAHEAD_S 0.0015
#define WIDTH_SMOOTH_S 0.01  // Width and boost changes glide instead of stepping.

typedef struct {
  LADSPA_Data *port[PORTS];
  unsigned long size, pos;  // Look-ahead window in frames, ring position.
  float *delay_l, *delay_r, *gains, *window;
  double sum, envelope, width, gain, rate;  // gain: the boost as a linear factor.
} Stage;

// A control value within its range; a non-finite one (never sent by PipeWire) means the fallback.
static double control(double v, double lo, double hi, double fallback) {
  return isfinite(v) ? fmin(hi, fmax(lo, v)) : fallback;
}

static LADSPA_Handle instantiate(const LADSPA_Descriptor *d, unsigned long rate) {
  (void)d;
  Stage *s = calloc(1, sizeof(Stage));
  if (!s) return NULL;
  s->rate = (double)rate;
  s->size = (unsigned long)lround(LOOKAHEAD_S * rate);
  if (s->size < 1) s->size = 1;
  s->delay_l = calloc(s->size, sizeof(float));
  s->delay_r = calloc(s->size, sizeof(float));
  s->gains = calloc(s->size, sizeof(float));
  s->window = calloc(s->size, sizeof(float));
  if (!s->delay_l || !s->delay_r || !s->gains || !s->window) {
    free(s->delay_l); free(s->delay_r); free(s->gains); free(s->window); free(s);
    return NULL;
  }
  return s;
}

static void activate(LADSPA_Handle h) {
  Stage *s = h;
  memset(s->delay_l, 0, s->size * sizeof(float));
  memset(s->delay_r, 0, s->size * sizeof(float));
  for (unsigned long i = 0; i < s->size; i++) s->gains[i] = s->window[i] = 1.0f;
  s->sum = (double)s->size;
  s->envelope = 1.0;
  s->pos = 0;
  s->width = s->port[WIDTH] ? control(*s->port[WIDTH], 0.0, 2.0, 1.0) : 1.0;
  s->gain = s->port[GAIN] ? pow(10.0, control(*s->port[GAIN], 0.0, 12.0, 0.0) / 20.0) : 1.0;
}

static void connect_port(LADSPA_Handle h, unsigned long port, LADSPA_Data *data) {
  if (port < PORTS) ((Stage *)h)->port[port] = data;
}

static void run(LADSPA_Handle h, unsigned long frames) {
  Stage *s = h;
  const LADSPA_Data *in_l = s->port[IN_L], *in_r = s->port[IN_R];
  LADSPA_Data *out_l = s->port[OUT_L], *out_r = s->port[OUT_R];
  const double width = control(*s->port[WIDTH], 0.0, 2.0, 1.0);
  const double boost = s->port[GAIN] ? pow(10.0, control(*s->port[GAIN], 0.0, 12.0, 0.0) / 20.0) : 1.0;
  const int requested = *s->port[LIMIT] > 0.5f;
  const double ceiling = pow(10.0, control(*s->port[CEILING], -12.0, 0.0, 0.0) / 20.0);
  const double release = exp(-1.0 / (control(*s->port[RELEASE], 10.0, 1000.0, 100.0) * 0.001 * s->rate));
  const double glide = 1.0 - exp(-1.0 / (WIDTH_SMOOTH_S * s->rate));
  double lowest = 1.0;
  for (unsigned long n = 0; n < frames; n++) {
    s->width += (width - s->width) * glide;
    s->gain += (boost - s->gain) * glide;
    // A boost is always limited, also while it glides back to 0 dB after the limiter was switched off.
    const int limit = requested || s->gain > 1.0001;
    double mid = 0.5 * ((double)in_l[n] + in_r[n]), side = 0.5 * ((double)in_l[n] - in_r[n]) * s->width;
    double l = (mid + side) * s->gain, r = (mid - side) * s->gain;
    if (!isfinite(l) || !isfinite(r)) l = r = 0.0;  // Never let a bad sample poison the state.
    unsigned long i = s->pos;
    s->delay_l[i] = (float)l;
    s->delay_r[i] = (float)r;
    double peak = fmax(fabs(l), fabs(r));
    // The gain is tracked even while the limiter is off, so switching it on never meets
    // loud frames already in the delay line unprepared.
    s->gains[i] = (float)(peak > ceiling ? ceiling / peak : 1.0);
    float hold = 1.0f;
    for (unsigned long k = 0; k < s->size; k++)
      if (s->gains[k] < hold) hold = s->gains[k];
    s->envelope = hold < s->envelope ? hold : hold + (s->envelope - hold) * release;
    float stored = (float)s->envelope;  // Add exactly what is later subtracted: no drift.
    s->sum += (double)stored - s->window[i];
    s->window[i] = stored;
    double gain = limit ? fmin(1.0, s->sum / (double)s->size) : 1.0;
    s->pos = (i + 1) % s->size;  // The oldest frame, written size - 1 frames ago.
    double y_l = s->delay_l[s->pos] * gain, y_r = s->delay_r[s->pos] * gain;
    if (limit) {  // Rounding in the running sum must never cross the ceiling.
      y_l = isfinite(y_l) ? fmin(ceiling, fmax(-ceiling, y_l)) : 0.0;
      y_r = isfinite(y_r) ? fmin(ceiling, fmax(-ceiling, y_r)) : 0.0;
    }
    out_l[n] = (LADSPA_Data)y_l;
    out_r[n] = (LADSPA_Data)y_r;
    if (gain < lowest) lowest = gain;
  }
  if (s->port[REDUCTION]) *s->port[REDUCTION] = (LADSPA_Data)(20.0 * log10(fmax(lowest, 1e-6)));
  if (s->port[LATENCY]) *s->port[LATENCY] = (LADSPA_Data)(s->size - 1);
}

static void cleanup(LADSPA_Handle h) {
  Stage *s = h;
  free(s->delay_l); free(s->delay_r); free(s->gains); free(s->window); free(s);
}

static const LADSPA_PortDescriptor kinds[PORTS] = {
  IN | AUDIO, IN | AUDIO, OUT | AUDIO, OUT | AUDIO,
  IN | CONTROL, IN | CONTROL, IN | CONTROL, IN | CONTROL, IN | CONTROL, OUT | CONTROL, OUT | CONTROL,
};
static const char *const names[PORTS] = {
  "in_l", "in_r", "out_l", "out_r", "width", "limit", "ceiling", "release", "gain", "reduction", "latency",
};
static const LADSPA_PortRangeHint hints[PORTS] = {
  {0, 0, 0}, {0, 0, 0}, {0, 0, 0}, {0, 0, 0},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_MIDDLE, 0.0f, 2.0f},
  // A number, not TOGGLED: PipeWire reports toggled ports as booleans, which the readback ignores.
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_1, 0.0f, 1.0f},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_MAXIMUM, -12.0f, 0.0f},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_100, 10.0f, 1000.0f},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_0, 0.0f, 12.0f},  // Volume boost in dB, before the limiter.
  {0, 0, 0}, {0, 0, 0},
};
static const LADSPA_Descriptor descriptor = {
  .UniqueID = 0, .Label = "deckyeq_stage", .Properties = 4 /* hard real-time capable */,
  .Name = "DeckyEQ stereo width, volume boost and look-ahead limiter", .Maker = "Rayek", .Copyright = "BSD-3-Clause",
  .PortCount = PORTS, .PortDescriptors = kinds, .PortNames = names, .PortRangeHints = hints,
  .instantiate = instantiate, .connect_port = connect_port, .activate = activate, .run = run,
  .cleanup = cleanup,
};

__attribute__((visibility("default"))) const LADSPA_Descriptor *ladspa_descriptor(unsigned long index) {
  return index == 0 ? &descriptor : NULL;
}
