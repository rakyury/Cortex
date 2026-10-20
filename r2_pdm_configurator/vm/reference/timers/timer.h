/**
 * @file timer.h
 * @brief Timer system - TON, TOF, TP, Edge-Triggered
 *
 * Standard PLC-style timer blocks:
 * - TON: Timer On-Delay (output after delay when input true)
 * - TOF: Timer Off-Delay (output stays on after input goes false)
 * - TP: Pulse Timer (fixed duration pulse on rising edge)
 */

#ifndef TIMER_H
#define TIMER_H

#include <stdint.h>
#include <stdbool.h>
#include "../channel_engine/channel.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Timer Types
 * ============================================================================ */

typedef enum {
    TIMER_TON = 0,      /* On-Delay: output goes true after delay */
    TIMER_TOF = 1,      /* Off-Delay: output stays true after input false */
    TIMER_TP  = 2,      /* Pulse: fixed pulse on rising edge */
    TIMER_BLINK = 3,    /* Blink: oscillate while input true */
    TIMER_ONESHOT = 4,  /* One-shot: single pulse, manual reset */
} TimerType_t;

typedef enum {
    TIMER_IDLE = 0,     /* Timer not running */
    TIMER_RUNNING = 1,  /* Timer counting */
    TIMER_DONE = 2,     /* Timer completed */
} TimerState_t;

/* ============================================================================
 * Timer Configuration
 * ============================================================================ */

typedef struct {
    uint16_t id;                /* Timer ID / output channel ID */
    uint8_t  type;              /* TimerType_t */
    uint8_t  flags;

    uint16_t input_channel;     /* Input trigger channel */
    uint16_t reset_channel;     /* Optional reset channel */

    uint32_t preset_ms;         /* Timer preset duration */
    uint32_t blink_on_ms;       /* Blink: on duration */
    uint32_t blink_off_ms;      /* Blink: off duration */

    char name[24];
} TimerConfig_t;

/* Timer flags */
#define TIMER_FLAG_ENABLED      (1 << 0)
#define TIMER_FLAG_RETRIGGER    (1 << 1)    /* TP: retrigger extends pulse */
#define TIMER_FLAG_INVERT_IN    (1 << 2)    /* Invert input */
#define TIMER_FLAG_INVERT_OUT   (1 << 3)    /* Invert output */

/* ============================================================================
 * Timer Runtime State
 * ============================================================================ */

typedef struct {
    uint8_t  state;             /* TimerState_t */
    uint8_t  output;            /* Current output (0 or 1) */
    uint8_t  prev_input;        /* Previous input for edge detection */
    uint8_t  blink_phase;       /* Blink: 0=off, 1=on */

    uint32_t start_tick;        /* Tick when timer started */
    uint32_t elapsed_ms;        /* Elapsed time */
    uint32_t blink_last_toggle; /* Last blink toggle tick */
} TimerRuntime_t;

/* ============================================================================
 * Timer Manager
 * ============================================================================ */

#ifndef MAX_TIMERS
#define MAX_TIMERS 12 /* STM32F446 profile; Racing Dash default is larger */
#endif

typedef struct {
    TimerConfig_t  config[MAX_TIMERS];
    TimerRuntime_t runtime[MAX_TIMERS];
    uint16_t       count;

    ChannelEngine_t* channels;

    bool initialized;
} TimerManager_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Initialize timer manager
 */
void Timer_Init(TimerManager_t* mgr, ChannelEngine_t* channels);

/**
 * @brief Reset all timers
 */
void Timer_Reset(TimerManager_t* mgr);

/**
 * @brief Add timer configuration
 * @return Timer index or -1 on error
 */
int Timer_Add(TimerManager_t* mgr, const TimerConfig_t* config);

/**
 * @brief Remove timer by ID
 */
void Timer_Remove(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Get timer by ID
 */
TimerConfig_t* Timer_GetById(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Update all timers (call every tick)
 */
void Timer_UpdateAll(TimerManager_t* mgr, uint32_t tick_ms);

/**
 * @brief Get timer elapsed time
 */
uint32_t Timer_GetElapsed(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Get timer remaining time
 */
uint32_t Timer_GetRemaining(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Get timer state
 */
TimerState_t Timer_GetState(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Get timer output
 */
bool Timer_GetOutput(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Force reset timer
 */
void Timer_ForceReset(TimerManager_t* mgr, uint16_t id);

/**
 * @brief Create timer config helper
 */
TimerConfig_t Timer_CreateConfig(uint16_t id, TimerType_t type,
                                  uint16_t input_ch, uint32_t preset_ms,
                                  const char* name);

#ifdef __cplusplus
}
#endif

#endif /* TIMER_H */
