/**
 * @file filter.h
 * @brief Signal filter system
 *
 * Filter types:
 * - SMA: Simple Moving Average
 * - EMA: Exponential Moving Average
 * - Median: Median filter (spike removal)
 * - LPF: Low-Pass Filter (1st order IIR)
 * - Slew: Slew rate limiter
 * - Deadband: Ignore small changes
 * - Hysteresis: Schmitt trigger
 */

#ifndef FILTER_H
#define FILTER_H

#include <stdint.h>
#include <stdbool.h>
#include "../channel_engine/channel.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Filter Types
 * ============================================================================ */

typedef enum {
    FILTER_NONE     = 0,
    FILTER_SMA      = 1,    /* Simple Moving Average */
    FILTER_EMA      = 2,    /* Exponential Moving Average */
    FILTER_MEDIAN   = 3,    /* Median filter */
    FILTER_LPF      = 4,    /* Low-pass filter (IIR) */
    FILTER_SLEW     = 5,    /* Slew rate limiter */
    FILTER_DEADBAND = 6,    /* Deadband (ignore small changes) */
    FILTER_HYST     = 7,    /* Hysteresis (Schmitt trigger) */
} FilterType_t;

/* ============================================================================
 * Filter Configuration
 * ============================================================================ */

#define FILTER_MAX_WINDOW 16

typedef struct {
    uint16_t id;                /* Filter ID / output channel ID */
    uint8_t  type;              /* FilterType_t */
    uint8_t  flags;

    uint16_t input_channel;     /* Input channel */
    uint16_t reserved;

    union {
        struct {                /* SMA */
            uint8_t window_size;
        } sma;

        struct {                /* EMA */
            uint8_t alpha;      /* 0-255 maps to 0.0-1.0 */
        } ema;

        struct {                /* Median */
            uint8_t window_size;/* 3, 5, 7, 9 */
        } median;

        struct {                /* LPF */
            uint16_t cutoff_x10;/* Cutoff freq × 10 (Hz) */
            uint16_t sample_rate;/* Sample rate (Hz) */
        } lpf;

        struct {                /* Slew */
            int32_t max_rise;   /* Max increase per sample */
            int32_t max_fall;   /* Max decrease per sample */
        } slew;

        struct {                /* Deadband */
            int32_t threshold;  /* Change threshold */
        } deadband;

        struct {                /* Hysteresis */
            int32_t threshold_high;
            int32_t threshold_low;
        } hyst;

        uint8_t raw[12];
    } params;

    char name[24];
} FilterConfig_t;

/* Filter flags */
#define FILTER_FLAG_ENABLED     (1 << 0)
#define FILTER_FLAG_INITIALIZED (1 << 1)

/* ============================================================================
 * Filter Runtime State
 * ============================================================================ */

typedef struct {
    int32_t output;             /* Current output value */
    int32_t prev_output;        /* Previous output */

    /* SMA/Median buffer */
    int32_t buffer[FILTER_MAX_WINDOW];
    uint8_t buf_idx;
    uint8_t buf_count;

    /* EMA/LPF state */
    int64_t accumulator;        /* Fixed-point accumulator */

    /* Hysteresis state */
    uint8_t hyst_state;         /* 0=low, 1=high */
} FilterRuntime_t;

/* ============================================================================
 * Filter Manager
 * ============================================================================ */

#ifndef MAX_FILTERS
#define MAX_FILTERS 12 /* STM32F446 profile; Racing Dash default is larger */
#endif

typedef struct {
    FilterConfig_t  config[MAX_FILTERS];
    FilterRuntime_t runtime[MAX_FILTERS];
    uint16_t        count;

    ChannelEngine_t* channels;

    bool initialized;
} FilterManager_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Initialize filter manager
 */
void Filter_Init(FilterManager_t* mgr, ChannelEngine_t* channels);

/**
 * @brief Reset all filters
 */
void Filter_Reset(FilterManager_t* mgr);

/**
 * @brief Add filter configuration
 * @return Filter index or -1 on error
 */
int Filter_Add(FilterManager_t* mgr, const FilterConfig_t* config);

/**
 * @brief Remove filter by ID
 */
void Filter_Remove(FilterManager_t* mgr, uint16_t id);

/**
 * @brief Update all filters (call every tick)
 */
void Filter_UpdateAll(FilterManager_t* mgr);

/**
 * @brief Get filter output
 */
int32_t Filter_GetOutput(FilterManager_t* mgr, uint16_t id);

/* ============================================================================
 * Filter Config Helpers
 * ============================================================================ */

FilterConfig_t Filter_CreateSMA(uint16_t id, uint16_t input_ch,
                                 uint8_t window_size, const char* name);

FilterConfig_t Filter_CreateEMA(uint16_t id, uint16_t input_ch,
                                 uint8_t alpha, const char* name);

FilterConfig_t Filter_CreateMedian(uint16_t id, uint16_t input_ch,
                                    uint8_t window_size, const char* name);

FilterConfig_t Filter_CreateLPF(uint16_t id, uint16_t input_ch,
                                 uint16_t cutoff_x10, uint16_t sample_rate,
                                 const char* name);

FilterConfig_t Filter_CreateSlew(uint16_t id, uint16_t input_ch,
                                  int32_t max_rise, int32_t max_fall,
                                  const char* name);

FilterConfig_t Filter_CreateDeadband(uint16_t id, uint16_t input_ch,
                                      int32_t threshold, const char* name);

FilterConfig_t Filter_CreateHysteresis(uint16_t id, uint16_t input_ch,
                                        int32_t high, int32_t low,
                                        const char* name);

#ifdef __cplusplus
}
#endif

#endif /* FILTER_H */
