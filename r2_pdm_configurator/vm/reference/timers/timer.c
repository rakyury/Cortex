/**
 * @file timer.c
 * @brief Timer system implementation
 */

#include "timer.h"
#include <string.h>

/* ============================================================================
 * Initialization
 * ============================================================================ */

void Timer_Init(TimerManager_t* mgr, ChannelEngine_t* channels)
{
    if (!mgr) return;

    memset(mgr, 0, sizeof(TimerManager_t));
    mgr->channels = channels;
    mgr->initialized = true;
}

void Timer_Reset(TimerManager_t* mgr)
{
    if (!mgr) return;

    for (uint16_t i = 0; i < mgr->count; i++) {
        TimerRuntime_t* rt = &mgr->runtime[i];
        rt->state = TIMER_IDLE;
        rt->output = 0;
        rt->prev_input = 0;
        rt->elapsed_ms = 0;
        rt->blink_phase = 0;
    }
}

/* ============================================================================
 * Timer Management
 * ============================================================================ */

int Timer_Add(TimerManager_t* mgr, const TimerConfig_t* config)
{
    if (!mgr || !config || mgr->count >= MAX_TIMERS) return -1;

    int idx = mgr->count++;
    memcpy(&mgr->config[idx], config, sizeof(TimerConfig_t));
    memset(&mgr->runtime[idx], 0, sizeof(TimerRuntime_t));

    return idx;
}

void Timer_Remove(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return;

    for (uint16_t i = 0; i < mgr->count; i++) {
        if (mgr->config[i].id == id) {
            mgr->config[i].flags &= ~TIMER_FLAG_ENABLED;
            break;
        }
    }
}

TimerConfig_t* Timer_GetById(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return NULL;

    for (uint16_t i = 0; i < mgr->count; i++) {
        if (mgr->config[i].id == id) {
            return &mgr->config[i];
        }
    }
    return NULL;
}

TimerConfig_t Timer_CreateConfig(uint16_t id, TimerType_t type,
                                  uint16_t input_ch, uint32_t preset_ms,
                                  const char* name)
{
    TimerConfig_t cfg = {0};
    cfg.id = id;
    cfg.type = (uint8_t)type;
    cfg.flags = TIMER_FLAG_ENABLED;
    cfg.input_channel = input_ch;
    cfg.preset_ms = preset_ms;
    cfg.blink_on_ms = 500;
    cfg.blink_off_ms = 500;

    if (name) {
        strncpy(cfg.name, name, sizeof(cfg.name) - 1);
    }

    return cfg;
}

/* ============================================================================
 * Timer Update Logic
 * ============================================================================ */

static void update_timer_ton(TimerConfig_t* cfg, TimerRuntime_t* rt,
                             bool input, uint32_t tick_ms)
{
    if (input) {
        if (rt->state == TIMER_IDLE) {
            rt->state = TIMER_RUNNING;
            rt->start_tick = tick_ms;
        }

        if (rt->state == TIMER_RUNNING) {
            rt->elapsed_ms = tick_ms - rt->start_tick;

            if (rt->elapsed_ms >= cfg->preset_ms) {
                rt->state = TIMER_DONE;
                rt->output = 1;
            }
        }
    } else {
        rt->state = TIMER_IDLE;
        rt->output = 0;
        rt->elapsed_ms = 0;
    }
}

static void update_timer_tof(TimerConfig_t* cfg, TimerRuntime_t* rt,
                             bool input, uint32_t tick_ms)
{
    if (input) {
        rt->state = TIMER_IDLE;
        rt->output = 1;
        rt->elapsed_ms = 0;
    } else {
        if (rt->output && rt->state == TIMER_IDLE) {
            rt->state = TIMER_RUNNING;
            rt->start_tick = tick_ms;
        }

        if (rt->state == TIMER_RUNNING) {
            rt->elapsed_ms = tick_ms - rt->start_tick;

            if (rt->elapsed_ms >= cfg->preset_ms) {
                rt->state = TIMER_DONE;
                rt->output = 0;
            }
        }
    }
}

static void update_timer_tp(TimerConfig_t* cfg, TimerRuntime_t* rt,
                            bool input, bool rising_edge, uint32_t tick_ms)
{
    if (rising_edge) {
        if (rt->state == TIMER_IDLE ||
            (cfg->flags & TIMER_FLAG_RETRIGGER)) {
            rt->state = TIMER_RUNNING;
            rt->start_tick = tick_ms;
            rt->output = 1;
        }
    }

    if (rt->state == TIMER_RUNNING) {
        rt->elapsed_ms = tick_ms - rt->start_tick;

        if (rt->elapsed_ms >= cfg->preset_ms) {
            rt->state = TIMER_DONE;
            rt->output = 0;
        }
    }

    if (rt->state == TIMER_DONE && !input) {
        rt->state = TIMER_IDLE;
        rt->elapsed_ms = 0;
    }
}

static void update_timer_blink(TimerConfig_t* cfg, TimerRuntime_t* rt,
                               bool input, uint32_t tick_ms)
{
    if (input) {
        if (rt->state == TIMER_IDLE) {
            rt->state = TIMER_RUNNING;
            rt->blink_phase = 1;
            rt->output = 1;
            rt->blink_last_toggle = tick_ms;
        }

        uint32_t phase_duration = rt->blink_phase ?
                                  cfg->blink_on_ms : cfg->blink_off_ms;

        if (tick_ms - rt->blink_last_toggle >= phase_duration) {
            rt->blink_phase = !rt->blink_phase;
            rt->output = rt->blink_phase;
            rt->blink_last_toggle = tick_ms;
        }
    } else {
        rt->state = TIMER_IDLE;
        rt->output = 0;
        rt->blink_phase = 0;
    }
}

static void update_timer_oneshot(TimerConfig_t* cfg, TimerRuntime_t* rt,
                                 bool input, bool rising_edge, uint32_t tick_ms,
                                 bool reset)
{
    (void)input; /* One-shot uses the rising edge, not continuous input. */
    if (reset) {
        rt->state = TIMER_IDLE;
        rt->output = 0;
        rt->elapsed_ms = 0;
        return;
    }

    if (rising_edge && rt->state == TIMER_IDLE) {
        rt->state = TIMER_RUNNING;
        rt->start_tick = tick_ms;
        rt->output = 1;
    }

    if (rt->state == TIMER_RUNNING) {
        rt->elapsed_ms = tick_ms - rt->start_tick;

        if (rt->elapsed_ms >= cfg->preset_ms) {
            rt->state = TIMER_DONE;
            rt->output = 0;
        }
    }
}

void Timer_UpdateAll(TimerManager_t* mgr, uint32_t tick_ms)
{
    if (!mgr || !mgr->channels) return;

    for (uint16_t i = 0; i < mgr->count; i++) {
        TimerConfig_t* cfg = &mgr->config[i];
        TimerRuntime_t* rt = &mgr->runtime[i];

        if (!(cfg->flags & TIMER_FLAG_ENABLED)) continue;

        int32_t input_val = Channel_GetValue(mgr->channels, cfg->input_channel);
        bool input = input_val != 0;

        if (cfg->flags & TIMER_FLAG_INVERT_IN) {
            input = !input;
        }

        bool rising_edge = input && !rt->prev_input;

        bool reset = false;
        if (cfg->reset_channel != 0) {
            reset = Channel_GetValue(mgr->channels, cfg->reset_channel) != 0;
        }

        switch ((TimerType_t)cfg->type) {
            case TIMER_TON:
                update_timer_ton(cfg, rt, input, tick_ms);
                break;

            case TIMER_TOF:
                update_timer_tof(cfg, rt, input, tick_ms);
                break;

            case TIMER_TP:
                update_timer_tp(cfg, rt, input, rising_edge, tick_ms);
                break;

            case TIMER_BLINK:
                update_timer_blink(cfg, rt, input, tick_ms);
                break;

            case TIMER_ONESHOT:
                update_timer_oneshot(cfg, rt, input, rising_edge, tick_ms, reset);
                break;
        }

        rt->prev_input = input ? 1 : 0;

        uint8_t output = rt->output;
        if (cfg->flags & TIMER_FLAG_INVERT_OUT) {
            output = !output;
        }

        Channel_SetValue(mgr->channels, cfg->id, output);
    }
}

/* ============================================================================
 * Timer Queries
 * ============================================================================ */

static int find_timer_index(TimerManager_t* mgr, uint16_t id)
{
    for (uint16_t i = 0; i < mgr->count; i++) {
        if (mgr->config[i].id == id) {
            return i;
        }
    }
    return -1;
}

uint32_t Timer_GetElapsed(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return 0;
    int idx = find_timer_index(mgr, id);
    return (idx >= 0) ? mgr->runtime[idx].elapsed_ms : 0;
}

uint32_t Timer_GetRemaining(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return 0;
    int idx = find_timer_index(mgr, id);
    if (idx < 0) return 0;

    uint32_t elapsed = mgr->runtime[idx].elapsed_ms;
    uint32_t preset = mgr->config[idx].preset_ms;

    return (elapsed < preset) ? (preset - elapsed) : 0;
}

TimerState_t Timer_GetState(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return TIMER_IDLE;
    int idx = find_timer_index(mgr, id);
    return (idx >= 0) ? (TimerState_t)mgr->runtime[idx].state : TIMER_IDLE;
}

bool Timer_GetOutput(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return false;
    int idx = find_timer_index(mgr, id);
    return (idx >= 0) ? mgr->runtime[idx].output != 0 : false;
}

void Timer_ForceReset(TimerManager_t* mgr, uint16_t id)
{
    if (!mgr) return;
    int idx = find_timer_index(mgr, id);
    if (idx < 0) return;

    TimerRuntime_t* rt = &mgr->runtime[idx];
    rt->state = TIMER_IDLE;
    rt->output = 0;
    rt->elapsed_ms = 0;
    rt->blink_phase = 0;
}
