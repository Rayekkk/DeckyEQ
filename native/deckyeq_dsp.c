// SPDX-License-Identifier: BSD-3-Clause
// DeckyEQ's stereo stage for PipeWire's filter-chain: input level and volume boost, stereo width,
// then a two-band look-ahead peak limiter. A LADSPA plugin, so it needs nothing beyond libc and libm.
//
// Each limiter delays the audio by LOOKAHEAD_S. For every input frame it computes the gain that
// keeps that frame under its ceiling and takes the minimum over the look-ahead window (a monotonic
// queue, constant cost per frame). The envelope follows that minimum down at once, holds it for
// HOLD_S so a low note is not traced cycle by cycle (which would distort it), then recovers with
// the release time, and is smoothed by a moving average as long as the window. The average of a
// window that only holds gains at or below a peak's own gain cannot exceed it, so the peak leaves
// the delay line under the ceiling, reached by a ramp instead of a click.
//
// The bass band (below BASS_HZ) is limited first, on its own and BASS_ROOM_DB below the ceiling,
// so a loud bass note does not turn down voices and everything else with it. A second limiter on
// the whole signal then guarantees the ceiling, also for peaks between samples (a DAC or a Bluetooth
// codec reconstructs them): its detector looks at the signal 4x oversampled (8-tap Lanczos), which
// needs DETECT_LAG more frames of delay; the hold keeps every such gain in force long enough. Both
// limiters always run: they change nothing below their ceilings.
#define _DEFAULT_SOURCE  // M_PI
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
       DEFAULT_0 = 0x200, DEFAULT_100 = 0x280 };
enum { IN_L, IN_R, OUT_L, OUT_R, LEVEL, WIDTH, CEILING, RELEASE, GAIN, REDUCTION, LATENCY, PORTS };

#define LOOKAHEAD_S 0.0015
#define HOLD_S 0.025  // Half a cycle at 20 Hz: the lowest notes are not traced either.
#define GLIDE_S 0.01  // Level, boost and width changes glide instead of stepping.
#define TAPS 8          // Interpolation taps; the points between samples DETECT_LAG and DETECT_LAG - 1 back.
#define DETECT_LAG 4
#define PHASES 3        // 1/4, 2/4 and 3/4 of the way between those two samples.
#define BASS_HZ 150.0   // The bass band: below it, a 4th-order (Linkwitz-Riley) low-pass.
#define BASS_ROOM_DB 3.0  // The bass band is limited this far below the ceiling, leaving room for the rest.

// One look-ahead gain computer: the minimum needed gain over the window (a monotonic queue),
// followed at once, held, released, and averaged over the window. It returns the gain for the
// frame pushed size - 1 frames ago, which never exceeds that frame's own needed gain.
typedef struct {
  unsigned long size, hold_frames, hold_left, q_head, q_count, pos;
  unsigned long long frame;
  float *window, *q_gain;
  unsigned long long *q_frame;
  double sum, envelope;
} Limiter;

static int limiter_alloc(Limiter *l, unsigned long size, unsigned long hold_frames) {
  l->size = size;
  l->hold_frames = hold_frames;
  l->window = calloc(size, sizeof(float));
  l->q_gain = calloc(size, sizeof(float));
  l->q_frame = calloc(size, sizeof(unsigned long long));
  return l->window && l->q_gain && l->q_frame;
}

static void limiter_free(Limiter *l) {
  free(l->window); free(l->q_gain); free(l->q_frame);
}

static void limiter_reset(Limiter *l) {
  for (unsigned long i = 0; i < l->size; i++) l->window[i] = 1.0f;
  l->sum = (double)l->size;
  l->envelope = 1.0;
  l->pos = l->hold_left = l->q_head = l->q_count = 0;
  l->frame = 0;
}

static double limiter_step(Limiter *l, float needed, double release) {
  // Expired entries leave the front first (so at most size - 1 remain), larger ones the back.
  while (l->q_count && l->q_frame[l->q_head] + l->size <= l->frame) {
    l->q_head = l->q_head + 1 == l->size ? 0 : l->q_head + 1;
    l->q_count--;
  }
  while (l->q_count) {
    unsigned long back = l->q_head + l->q_count - 1;
    if (l->q_gain[back >= l->size ? back - l->size : back] < needed) break;
    l->q_count--;
  }
  unsigned long tail = l->q_head + l->q_count;
  if (tail >= l->size) tail -= l->size;
  l->q_gain[tail] = needed;
  l->q_frame[tail] = l->frame;
  l->q_count++;
  double minimum = l->q_gain[l->q_head];
  if (minimum <= l->envelope) {
    l->envelope = minimum;
    l->hold_left = l->hold_frames;
  } else if (l->hold_left) {
    l->hold_left--;
  } else {
    l->envelope = minimum + (l->envelope - minimum) * release;
  }
  float stored = (float)l->envelope;  // Add exactly what is later subtracted: no drift.
  l->sum += (double)stored - l->window[l->pos];
  l->window[l->pos] = stored;
  l->pos = l->pos + 1 == l->size ? 0 : l->pos + 1;
  l->frame++;
  return fmin(1.0, l->sum / (double)l->size);
}

// A Butterworth section (RBJ, Q = 1/sqrt(2)), transposed direct form II, in double. Two in series
// make a 4th-order Linkwitz-Riley filter; its low-pass and high-pass sum to a flat (all-pass) response.
typedef struct { double b0, b1, b2, a1, a2, z1, z2; } Section;

static void butterworth(Section *f, int high, double hz, double rate) {
  double w = 2.0 * M_PI * hz / rate, alpha = sin(w) / (2.0 * M_SQRT1_2), c = cos(w), a0 = 1.0 + alpha;
  f->b0 = f->b2 = (high ? (1.0 + c) : (1.0 - c)) / 2.0 / a0;
  f->b1 = (high ? -(1.0 + c) : (1.0 - c)) / a0;
  f->a1 = -2.0 * c / a0;
  f->a2 = (1.0 - alpha) / a0;
  f->z1 = f->z2 = 0.0;
}

static double section_run(Section *f, double x) {
  double y = f->b0 * x + f->z1;
  f->z1 = f->b1 * x - f->a1 * y + f->z2;
  f->z2 = f->b2 * x - f->a2 * y;
  return y;
}

typedef struct {
  LADSPA_Data *port[PORTS];
  unsigned long size;                               // Look-ahead window in frames.
  Limiter bass, full;                               // The bass band's limiter, then the whole signal's.
  Section low[2][2], high[2][2];                    // [channel][section]: the bands, Linkwitz-Riley.
  float *high_l, *high_r, *low_l, *low_r;           // The two bands, delayed size - 1 frames.
  unsigned long split_pos;
  float *delay_l, *delay_r;                         // The second delay: size - 1 + DETECT_LAG frames.
  unsigned long delay_size, delay_pos, history_pos;
  double history_l[TAPS], history_r[TAPS];          // The last TAPS samples, for the oversampled peak.
  double weights[PHASES][TAPS];                     // Lanczos (a = 4) interpolation weights.
  double width, gain, rate, glide;
  double last_ceiling_db, ceiling, last_release_ms, release;  // Cached: no pow/exp per block.
} Stage;

static double lanczos(double x) {
  if (fabs(x) < 1e-12) return 1.0;
  if (fabs(x) >= 4.0) return 0.0;
  double px = M_PI * x;
  return 4.0 * sin(px) * sin(px / 4.0) / (px * px);
}

// A control value within its range; an unconnected or non-finite one means the fallback.
static double control(const Stage *s, int port, double lo, double hi, double fallback) {
  if (!s->port[port]) return fallback;
  double v = *s->port[port];
  return isfinite(v) ? fmin(hi, fmax(lo, v)) : fallback;
}

static double target_gain(const Stage *s) {
  return pow(10.0, (control(s, LEVEL, -96.0, 24.0, 0.0) + control(s, GAIN, 0.0, 12.0, 0.0)) / 20.0);
}

static void stage_free(Stage *s) {
  limiter_free(&s->bass); limiter_free(&s->full);
  free(s->high_l); free(s->high_r); free(s->low_l); free(s->low_r); free(s->delay_l); free(s->delay_r);
  free(s);
}

static LADSPA_Handle instantiate(const LADSPA_Descriptor *d, unsigned long rate) {
  (void)d;
  Stage *s = calloc(1, sizeof(Stage));
  if (!s) return NULL;
  s->rate = (double)rate;
  s->size = (unsigned long)lround(LOOKAHEAD_S * rate);
  if (s->size < 1) s->size = 1;
  s->glide = 1.0 - exp(-1.0 / (GLIDE_S * s->rate));
  s->delay_size = s->size + DETECT_LAG;
  for (int p = 0; p < PHASES; p++)  // Point (p + 1) / 4 past the sample DETECT_LAG back, from the oldest tap.
    for (int k = 0; k < TAPS; k++) s->weights[p][k] = lanczos((TAPS - 1 - DETECT_LAG) + (p + 1) / 4.0 - k);
  unsigned long hold = (unsigned long)lround(HOLD_S * rate);
  int ok = limiter_alloc(&s->bass, s->size, hold) & limiter_alloc(&s->full, s->size, hold);
  s->high_l = calloc(s->size, sizeof(float)); s->high_r = calloc(s->size, sizeof(float));
  s->low_l = calloc(s->size, sizeof(float)); s->low_r = calloc(s->size, sizeof(float));
  s->delay_l = calloc(s->delay_size, sizeof(float)); s->delay_r = calloc(s->delay_size, sizeof(float));
  if (!ok || !s->high_l || !s->high_r || !s->low_l || !s->low_r || !s->delay_l || !s->delay_r) {
    stage_free(s);
    return NULL;
  }
  return s;
}

static void reset_split(Stage *s) {
  // Below Nyquist at any rate; at very low rates the band simply covers less.
  double hz = fmin(BASS_HZ, 0.45 * s->rate);
  for (int c = 0; c < 2; c++)
    for (int k = 0; k < 2; k++) {
      butterworth(&s->low[c][k], 0, hz, s->rate);
      butterworth(&s->high[c][k], 1, hz, s->rate);
    }
}

static void activate(LADSPA_Handle h) {
  Stage *s = h;
  limiter_reset(&s->bass);
  limiter_reset(&s->full);
  reset_split(s);
  memset(s->high_l, 0, s->size * sizeof(float)); memset(s->high_r, 0, s->size * sizeof(float));
  memset(s->low_l, 0, s->size * sizeof(float)); memset(s->low_r, 0, s->size * sizeof(float));
  memset(s->delay_l, 0, s->delay_size * sizeof(float));
  memset(s->delay_r, 0, s->delay_size * sizeof(float));
  memset(s->history_l, 0, sizeof s->history_l);
  memset(s->history_r, 0, sizeof s->history_r);
  s->split_pos = s->delay_pos = s->history_pos = 0;
  s->width = control(s, WIDTH, 0.0, 2.0, 1.0);
  s->gain = target_gain(s);
  s->last_ceiling_db = s->last_release_ms = NAN;  // Recomputed on the first run.
}

static void connect_port(LADSPA_Handle h, unsigned long port, LADSPA_Data *data) {
  if (port < PORTS) ((Stage *)h)->port[port] = data;
}

// Glide toward target; snap once within reach, so the value never decays into subnormals.
static double glide_to(double value, double target, double rate) {
  double next = value + (target - value) * rate;
  return fabs(target - next) < 1e-9 ? target : next;
}

static void run(LADSPA_Handle h, unsigned long frames) {
  Stage *s = h;
  const LADSPA_Data *in_l = s->port[IN_L], *in_r = s->port[IN_R];
  LADSPA_Data *out_l = s->port[OUT_L], *out_r = s->port[OUT_R];
  if (!in_l || !in_r || !out_l || !out_r) return;
  const double width = control(s, WIDTH, 0.0, 2.0, 1.0), gain_target = target_gain(s);
  const double ceiling_db = control(s, CEILING, -12.0, 0.0, -1.0);
  const double release_ms = control(s, RELEASE, 10.0, 1000.0, 100.0);
  if (ceiling_db != s->last_ceiling_db) {
    s->last_ceiling_db = ceiling_db;
    s->ceiling = pow(10.0, ceiling_db / 20.0);
  }
  if (release_ms != s->last_release_ms) {
    s->last_release_ms = release_ms;
    s->release = exp(-1.0 / (release_ms * 0.001 * s->rate));
  }
  const double ceiling = s->ceiling, bass_ceiling = ceiling * pow(10.0, -BASS_ROOM_DB / 20.0);
  double lowest = 1.0;
  for (unsigned long n = 0; n < frames; n++) {
    s->width = glide_to(s->width, width, s->glide);
    s->gain = glide_to(s->gain, gain_target, s->glide);
    double mid = 0.5 * ((double)in_l[n] + in_r[n]), side = 0.5 * ((double)in_l[n] - in_r[n]) * s->width;
    double l = (mid + side) * s->gain, r = (mid - side) * s->gain;
    if (!isfinite(l) || !isfinite(r)) l = r = 0.0;  // Never let a bad sample poison the state.

    // 1. The bass band gets its own limiter, so a loud bass note turns down only the bass. With
    // it idle the two bands add up to the input with a flat response (only the phase of the
    // lowest notes turns, which is inaudible).
    double low_l = section_run(&s->low[0][1], section_run(&s->low[0][0], l));
    double low_r = section_run(&s->low[1][1], section_run(&s->low[1][0], r));
    double high_l = section_run(&s->high[0][1], section_run(&s->high[0][0], l));
    double high_r = section_run(&s->high[1][1], section_run(&s->high[1][0], r));
    if (!isfinite(low_l + low_r + high_l + high_r)) {  // Absurd input overflowed a filter: start afresh.
      reset_split(s);
      low_l = low_r = high_l = high_r = 0.0;
    }
    unsigned long i = s->split_pos;
    s->high_l[i] = (float)high_l; s->high_r[i] = (float)high_r;
    s->low_l[i] = (float)low_l; s->low_r[i] = (float)low_r;
    double bass_peak = fmax(fabs(low_l), fabs(low_r));
    double bass_gain = limiter_step(&s->bass, (float)(bass_peak > bass_ceiling ? bass_ceiling / bass_peak : 1.0),
                                    s->release);
    s->split_pos = i + 1 == s->size ? 0 : i + 1;  // The oldest frame, written size - 1 frames ago.
    unsigned long o = s->split_pos;
    l = s->high_l[o] + bass_gain * s->low_l[o];
    r = s->high_r[o] + bass_gain * s->low_r[o];

    // 2. The whole signal, including peaks between samples, stays under the ceiling.
    s->delay_l[s->delay_pos] = (float)l;
    s->delay_r[s->delay_pos] = (float)r;
    s->history_l[s->history_pos] = l;
    s->history_r[s->history_pos] = r;
    s->history_pos = (s->history_pos + 1) % TAPS;  // Now the oldest tap.
    double peak = fmax(fabs(l), fabs(r));
    for (int p = 0; p < PHASES; p++) {
      double il = 0.0, ir = 0.0;
      for (int k = 0; k < TAPS; k++) {
        unsigned long t = (s->history_pos + k) % TAPS;
        il += s->weights[p][k] * s->history_l[t];
        ir += s->weights[p][k] * s->history_r[t];
      }
      peak = fmax(peak, fmax(fabs(il), fabs(ir)));
    }
    double gain = limiter_step(&s->full, (float)(peak > ceiling ? ceiling / peak : 1.0), s->release);
    s->delay_pos = s->delay_pos + 1 == s->delay_size ? 0 : s->delay_pos + 1;  // The oldest frame.
    // Rounding in the running sum must never cross the ceiling.
    double y_l = s->delay_l[s->delay_pos] * gain, y_r = s->delay_r[s->delay_pos] * gain;
    out_l[n] = (LADSPA_Data)(isfinite(y_l) ? fmin(ceiling, fmax(-ceiling, y_l)) : 0.0);
    out_r[n] = (LADSPA_Data)(isfinite(y_r) ? fmin(ceiling, fmax(-ceiling, y_r)) : 0.0);
    if (gain < lowest) lowest = gain;
  }
  if (s->port[REDUCTION]) *s->port[REDUCTION] = (LADSPA_Data)(20.0 * log10(fmax(lowest, 1e-6)));
  if (s->port[LATENCY]) *s->port[LATENCY] = (LADSPA_Data)((s->size - 1) + (s->delay_size - 1));
}

static void cleanup(LADSPA_Handle h) {
  stage_free(h);
}

static const LADSPA_PortDescriptor kinds[PORTS] = {
  IN | AUDIO, IN | AUDIO, OUT | AUDIO, OUT | AUDIO,
  IN | CONTROL, IN | CONTROL, IN | CONTROL, IN | CONTROL, IN | CONTROL, OUT | CONTROL, OUT | CONTROL,
};
static const char *const names[PORTS] = {
  "in_l", "in_r", "out_l", "out_r", "level", "width", "ceiling", "release", "gain", "reduction", "latency",
};
// Ranges mirror deckyeq/limits.json; tests/test_dsp.py checks that they agree.
static const LADSPA_PortRangeHint hints[PORTS] = {
  {0, 0, 0}, {0, 0, 0}, {0, 0, 0}, {0, 0, 0},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_0, -96.0f, 24.0f},  // Input level in dB, incl. headroom.
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_MIDDLE, 0.0f, 2.0f},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_MAXIMUM, -12.0f, 0.0f},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_100, 10.0f, 1000.0f},
  {BOUNDED_BELOW | BOUNDED_ABOVE | DEFAULT_0, 0.0f, 12.0f},  // Volume boost in dB.
  {0, 0, 0}, {0, 0, 0},
};
static const LADSPA_Descriptor descriptor = {
  .UniqueID = 0, .Label = "deckyeq_stage", .Properties = 4 /* hard real-time capable */,
  .Name = "DeckyEQ level, stereo width, volume boost and two-band look-ahead limiter", .Maker = "Rayek",
  .Copyright = "BSD-3-Clause",
  .PortCount = PORTS, .PortDescriptors = kinds, .PortNames = names, .PortRangeHints = hints,
  .instantiate = instantiate, .connect_port = connect_port, .activate = activate, .run = run,
  .cleanup = cleanup,
};

__attribute__((visibility("default"))) const LADSPA_Descriptor *ladspa_descriptor(unsigned long index) {
  return index == 0 ? &descriptor : NULL;
}
