/**
 * @file filter.c
 * @brief Signal filter implementation
 */

#include "filter.h"
#include <string.h>

/* ============================================================================
 * Initialization
 * ============================================================================ */

void Filter_Init(FilterManager_t* mgr, ChannelEngine_t* channels)
{
    if (!mgr) return;

    memset(mgr, 0, sizeof(FilterManager_t));
    mgr->channels = channels;
    mgr->initialized = true;
}

void Filter_Reset(FilterManager_t* mgr)
{
    if (!mgr) return;

    for (uint16_t i = 0; i < mgr->count; i++) {
        FilterRuntime_t* rt = &mgr->runtime[i];
        memset(rt, 0, sizeof(FilterRuntime_t));
        mgr->config[i].flags &= ~FILTER_FLAG_INITIALIZED;
    }
}

/* ============================================================================
 * Filter Management
 * ============================================================================ */

int Filter_Add(FilterManager_t* mgr, const FilterConfig_t* config)
{
    if (!mgr || !config || mgr->count >= MAX_FILTERS) return -1;

    int idx = mgr->count++;
    memcpy(&mgr->config[idx], config, sizeof(FilterConfig_t));
    memset(&mgr->runtime[idx], 0, sizeof(FilterRuntime_t));

    return idx;
}

void Filter_Remove(FilterManager_t* mgr, uint16_t id)
{
    if (!mgr) return;

    for (uint16_t i = 0; i < mgr->count; i++) {
        if (mgr->config[i].id == id) {
            mgr->config[i].flags &= ~FILTER_FLAG_ENABLED;
            break;
        }
    }
}

/* ============================================================================
 * Filter Algorithms
 * ============================================================================ */

static void filter_sma(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    uint8_t size = cfg->params.sma.window_size;
    if (size == 0 || size > FILTER_MAX_WINDOW) size = 4;

    rt->buffer[rt->buf_idx] = input;
    rt->buf_idx = (rt->buf_idx + 1) % size;
    if (rt->buf_count < size) rt->buf_count++;

    int64_t sum = 0;
    for (uint8_t i = 0; i < rt->buf_count; i++) {
        sum += rt->buffer[i];
    }
    rt->output = (int32_t)(sum / rt->buf_count);
}

static void filter_ema(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    if (!(cfg->flags & FILTER_FLAG_INITIALIZED)) {
        rt->accumulator = (int64_t)input * 65536;   /* Q16; << of a negative is UB */
        cfg->flags |= FILTER_FLAG_INITIALIZED;
    }

    uint32_t alpha = cfg->params.ema.alpha;
    int64_t input_scaled = (int64_t)input * 65536;

    rt->accumulator = (rt->accumulator * (255 - alpha) + input_scaled * alpha) / 255;
    /* round, do not floor: the Q16 accumulator stalls a few 1/65536 below a
     * steady input once the step rounds to zero, and a floored output then
     * reads one unit low forever (399 for a 400 input, "4.19 bar") */
    rt->output = (int32_t)((rt->accumulator + (1 << 15)) >> 16);
}

static void sort_buffer(int32_t* arr, uint8_t len)
{
    for (uint8_t i = 0; i < len - 1; i++) {
        for (uint8_t j = 0; j < len - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int32_t tmp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = tmp;
            }
        }
    }
}

static void filter_median(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    uint8_t size = cfg->params.median.window_size;
    if (size < 3) size = 3;
    if (size > FILTER_MAX_WINDOW) size = FILTER_MAX_WINDOW;
    if (size % 2 == 0) size--;

    rt->buffer[rt->buf_idx] = input;
    rt->buf_idx = (rt->buf_idx + 1) % size;
    if (rt->buf_count < size) rt->buf_count++;

    int32_t sorted[FILTER_MAX_WINDOW];
    memcpy(sorted, rt->buffer, rt->buf_count * sizeof(int32_t));
    sort_buffer(sorted, rt->buf_count);

    rt->output = sorted[rt->buf_count / 2];
}

static void filter_lpf(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    if (!(cfg->flags & FILTER_FLAG_INITIALIZED)) {
        rt->accumulator = (int64_t)input * 65536;   /* Q16; << of a negative is UB */
        cfg->flags |= FILTER_FLAG_INITIALIZED;
    }

    uint16_t cutoff = cfg->params.lpf.cutoff_x10;
    uint16_t sample_rate = cfg->params.lpf.sample_rate;
    if (sample_rate == 0) sample_rate = 100;
    if (cutoff == 0) cutoff = 10;

    uint32_t alpha = (cutoff * 256) / (sample_rate * 10);
    if (alpha > 255) alpha = 255;

    int64_t input_scaled = (int64_t)input * 65536;
    rt->accumulator = (rt->accumulator * (256 - alpha) + input_scaled * alpha) / 256;
    rt->output = (int32_t)(rt->accumulator >> 16);
}

static void filter_slew(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    if (!(cfg->flags & FILTER_FLAG_INITIALIZED)) {
        rt->output = input;
        cfg->flags |= FILTER_FLAG_INITIALIZED;
        return;
    }

    /* 64-bit: input - output and the step overflowed int32 (UB) for
     * inputs far apart (sensor fault values, INT32 extremes) */
    int64_t delta = (int64_t)input - rt->output;

    if (delta > cfg->params.slew.max_rise) {
        rt->output = (int32_t)((int64_t)rt->output + cfg->params.slew.max_rise);
    } else if (delta < -(int64_t)cfg->params.slew.max_fall) {
        rt->output = (int32_t)((int64_t)rt->output - cfg->params.slew.max_fall);
    } else {
        rt->output = input;
    }
}

static void filter_deadband(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    if (!(cfg->flags & FILTER_FLAG_INITIALIZED)) {
        rt->output = input;
        cfg->flags |= FILTER_FLAG_INITIALIZED;
        return;
    }

    int64_t delta = (int64_t)input - rt->output;
    if (delta < 0) delta = -delta;

    if (delta > cfg->params.deadband.threshold) {
        rt->output = input;
    }
}

static void filter_hysteresis(FilterConfig_t* cfg, FilterRuntime_t* rt, int32_t input)
{
    if (rt->hyst_state == 0) {
        if (input >= cfg->params.hyst.threshold_high) {
            rt->hyst_state = 1;
            rt->output = 1;
        } else {
            rt->output = 0;
        }
    } else {
        if (input <= cfg->params.hyst.threshold_low) {
            rt->hyst_state = 0;
            rt->output = 0;
        } else {
            rt->output = 1;
        }
    }
}

/* ============================================================================
 * Update All Filters
 * ============================================================================ */

void Filter_UpdateAll(FilterManager_t* mgr)
{
    if (!mgr || !mgr->channels) return;

    for (uint16_t i = 0; i < mgr->count; i++) {
        FilterConfig_t* cfg = &mgr->config[i];
        FilterRuntime_t* rt = &mgr->runtime[i];

        if (!(cfg->flags & FILTER_FLAG_ENABLED)) continue;

        int32_t input = Channel_GetValue(mgr->channels, cfg->input_channel);
        rt->prev_output = rt->output;

        switch ((FilterType_t)cfg->type) {
            case FILTER_SMA:
                filter_sma(cfg, rt, input);
                break;

            case FILTER_EMA:
                filter_ema(cfg, rt, input);
                break;

            case FILTER_MEDIAN:
                filter_median(cfg, rt, input);
                break;

            case FILTER_LPF:
                filter_lpf(cfg, rt, input);
                break;

            case FILTER_SLEW:
                filter_slew(cfg, rt, input);
                break;

            case FILTER_DEADBAND:
                filter_deadband(cfg, rt, input);
                break;

            case FILTER_HYST:
                filter_hysteresis(cfg, rt, input);
                break;

            default:
                rt->output = input;
                break;
        }

        Channel_SetValue(mgr->channels, cfg->id, rt->output);
    }
}

int32_t Filter_GetOutput(FilterManager_t* mgr, uint16_t id)
{
    if (!mgr) return 0;

    for (uint16_t i = 0; i < mgr->count; i++) {
        if (mgr->config[i].id == id) {
            return mgr->runtime[i].output;
        }
    }
    return 0;
}

/* ============================================================================
 * Config Helpers
 * ============================================================================ */

FilterConfig_t Filter_CreateSMA(uint16_t id, uint16_t input_ch,
                                 uint8_t window_size, const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_SMA;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.sma.window_size = window_size;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}

FilterConfig_t Filter_CreateEMA(uint16_t id, uint16_t input_ch,
                                 uint8_t alpha, const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_EMA;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.ema.alpha = alpha;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}

FilterConfig_t Filter_CreateMedian(uint16_t id, uint16_t input_ch,
                                    uint8_t window_size, const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_MEDIAN;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.median.window_size = window_size;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}

FilterConfig_t Filter_CreateLPF(uint16_t id, uint16_t input_ch,
                                 uint16_t cutoff_x10, uint16_t sample_rate,
                                 const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_LPF;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.lpf.cutoff_x10 = cutoff_x10;
    cfg.params.lpf.sample_rate = sample_rate;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}

FilterConfig_t Filter_CreateSlew(uint16_t id, uint16_t input_ch,
                                  int32_t max_rise, int32_t max_fall,
                                  const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_SLEW;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.slew.max_rise = max_rise;
    cfg.params.slew.max_fall = max_fall;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}

FilterConfig_t Filter_CreateDeadband(uint16_t id, uint16_t input_ch,
                                      int32_t threshold, const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_DEADBAND;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.deadband.threshold = threshold;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}

FilterConfig_t Filter_CreateHysteresis(uint16_t id, uint16_t input_ch,
                                        int32_t high, int32_t low,
                                        const char* name)
{
    FilterConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = FILTER_HYST;
    cfg.flags = FILTER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.params.hyst.threshold_high = high;
    cfg.params.hyst.threshold_low = low;
    if (name) strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    return cfg;
}
