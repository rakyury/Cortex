/**
 * @file channel.h
 * @brief Channel Engine - Core dataflow system
 *
 * All widget bindings go through channels. Three classes:
 * - System: hardcoded in firmware (timebase, screen, brightness)
 * - Input: CAN/analog signals decoded into channels
 * - User: computed via VM bytecode from other channels
 */

#ifndef CHANNEL_H
#define CHANNEL_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Channel Types and Classes
 * ============================================================================ */

typedef enum {
    CH_TYPE_NUMBER = 0,     // int32 value
    CH_TYPE_BOOL = 1,       // 0 or 1
    CH_TYPE_NUMSTATE = 2,   // enum state (0-255)
} ChannelType_t;

typedef enum {
    CH_CLASS_SYSTEM = 0,    // Hardcoded in firmware
    CH_CLASS_INPUT = 1,     // CAN/analog input
    CH_CLASS_USER = 2,      // Computed via VM
    CH_CLASS_PEAK = 3,      // Peak readout of another channel (CH_PEAK_* ids)
} ChannelClass_t;

/* ============================================================================
 * Channel Structure
 * ============================================================================ */

#ifndef MAX_CHANNELS
#define MAX_CHANNELS 96 /* STM32F446 profile; Racing Dash default is larger */
#endif
#define MAX_CHANNEL_NAME    24

typedef struct {
    uint16_t id;
    uint8_t  type;          // ChannelType_t
    uint8_t  ch_class;      // ChannelClass_t
    int32_t  value;
    int32_t  prev_value;    // for edge detection
    uint32_t updated_tick;  // for stale detection
    uint8_t  flags;
    uint8_t  reserved;
    uint16_t peak_src;      // CH_CLASS_PEAK: engine index of the source
    char     name[MAX_CHANNEL_NAME];
    int32_t  peak_min;      // lowest / highest value since the last peak reset
    int32_t  peak_max;      // (valid while CH_FLAG_PEAK_SEEDED)
} Channel_t;

/* Channel flags */
#define CH_FLAG_VALID       (1 << 0)    // Channel has valid value
#define CH_FLAG_STALE       (1 << 1)    // Value is stale (timeout)
#define CH_FLAG_CHANGED     (1 << 2)    // Value changed this tick
#define CH_FLAG_RISING      (1 << 3)    // Rising edge detected
#define CH_FLAG_FALLING     (1 << 4)    // Falling edge detected
#define CH_FLAG_FED         (1 << 5)    // Input got a value since the engine reset
#define CH_FLAG_PEAK_SEEDED (1 << 6)    // peak_min / peak_max hold real samples
#define CH_FLAG_SEED        (1 << 7)    // input holds the design's seed (preview value,
                                        // blob 'SEED' section) - no real sample yet; NOT
                                        // fed, no peaks; the first real value clears it

/* ============================================================================
 * Peaks: every input / user channel keeps its min and max since the last
 * reset. A peak is addressed by a derived id: max of X = CH_PEAK_MAX_BASE + X,
 * min of X = CH_PEAK_MIN_BASE + X (X < CH_PEAK_SRC_LIMIT). Channel_GetValue
 * answers such an id for ANY channel; the configurator registers the ones a
 * design uses (class CH_CLASS_PEAK) so they reach the M7, log and telemetry.
 * Inputs start tracking at their first received value, user channels once
 * any input is live (or right away in a design without inputs); an
 * unseeded peak reads as the channel's current value.
 * ============================================================================ */
#define CH_PEAK_SRC_LIMIT       0x2000u     /* sources 0..8191 */
#define CH_PEAK_MAX_BASE        0xA000u     /* 40960..49151 */
#define CH_PEAK_MIN_BASE        0xC000u     /* 49152..57343 */
#define CH_IS_PEAK_ID(id)       ((id) >= CH_PEAK_MAX_BASE && (id) < CH_PEAK_MIN_BASE + CH_PEAK_SRC_LIMIT)
#define CH_PEAK_SOURCE(id)      ((uint16_t)((id) & (CH_PEAK_SRC_LIMIT - 1u)))
#define CH_PEAK_IS_MIN(id)      ((id) >= CH_PEAK_MIN_BASE)
#define CH_PEAK_RESET_ALL       0u          /* Channel_ResetPeaks: every channel */
#define CH_PEAK_SRC_NONE        0xFFFFu     /* peak_src: not resolved yet */
#define CH_PEAK_SRC_MISSING     0xFFFEu     /* peak_src: source not in the design */

/* ============================================================================
 * System Channel IDs (reserved range 0-99)
 * ============================================================================ */

#define CH_SYS_ZERO             0       // Constant 0
#define CH_SYS_ONE              1       // Constant 1
#define CH_SYS_TIMEBASE_MS      10      // Time since boot (ms)
#define CH_SYS_DT_MS            11      // Frame delta time (ms)
#define CH_SYS_CURRENT_SCREEN   20      // Active screen ID
#define CH_SYS_BRIGHTNESS       21      // Display brightness 0-100
#define CH_SYS_WARNING_STATE    30      // Global warning state
#define CH_SYS_FAULT_STATE      31      // Global fault state

#define CH_SYS_RESERVED_END     99      // System channels 0-99

/* Input channels start at 100 */
#define CH_INPUT_START          100

/* User channels start at 1000 */
#define CH_USER_START           1000

/* ============================================================================
 * Channel Engine State
 * ============================================================================ */

typedef struct {
    Channel_t channels[MAX_CHANNELS];
    uint16_t  channel_count;
    uint32_t  current_tick;
    uint32_t  dt_ms;
    bool      initialized;
    bool      peak_armed;       /* user channels track peaks (an input is live) */
    uint16_t  peak_channels;    /* registered CH_CLASS_PEAK channels */
    uint32_t  peak_seq;         /* bumps on every peak change (persistence) */
    uint16_t  seed_count;       /* inputs still holding a design seed (CH_FLAG_SEED):
                                   user-channel peaks wait until it is 0 */
} ChannelEngine_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Initialize channel engine
 */
void Channel_Init(ChannelEngine_t* engine);

/**
 * @brief Reset all channels to defaults
 */
void Channel_Reset(ChannelEngine_t* engine);

/**
 * @brief Register a new channel
 * @return Channel pointer or NULL if full
 */
Channel_t* Channel_Register(ChannelEngine_t* engine, uint16_t id,
                            ChannelType_t type, ChannelClass_t ch_class,
                            const char* name);

/**
 * @brief Find channel by ID
 * @return Channel pointer or NULL if not found
 */
Channel_t* Channel_GetById(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Get channel value
 * @return Value or 0 if not found
 */
int32_t Channel_GetValue(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Set channel value (marks as changed if different)
 */
void Channel_SetValue(ChannelEngine_t* engine, uint16_t id, int32_t value);

/**
 * @brief Set channel value with tick update
 */
void Channel_SetValueWithTick(ChannelEngine_t* engine, uint16_t id,
                               int32_t value, uint32_t tick);

/* ============================================================================
 * Seeds: an INPUT channel shows the design's preview value until its first
 * real sample (blob 'SEED' section, docs/architecture.md). A seed is not a
 * sample: the channel is not FED, its peaks do not latch it, user-channel
 * peaks wait for the last seed to go. Any Channel_SetValue clears it.
 * ============================================================================ */

/** Seed an input that has no real value yet; false when the id is not an
 *  input or already got a sample */
bool Channel_Seed(ChannelEngine_t* engine, uint16_t id, int32_t value);

/** Every input still holding a seed falls back to 0 (unfed, as if the
 *  design had no seeds); returns how many */
uint16_t Channel_DropSeeds(ChannelEngine_t* engine);

/** The channel holds a design seed (no real sample yet) */
bool Channel_IsSeeded(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Check if channel value changed this tick
 */
bool Channel_HasChanged(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Check for rising edge (false→true)
 */
bool Channel_RisingEdge(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Check for falling edge (true→false)
 */
bool Channel_FallingEdge(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Begin new tick - updates dt, clears edge flags, saves prev values
 */
void Channel_BeginTick(ChannelEngine_t* engine, uint32_t tick_ms);

/**
 * @brief End tick - detect edges based on value changes
 */
void Channel_EndTick(ChannelEngine_t* engine);

/**
 * @brief Check if channel is stale (no update for timeout_ms)
 */
bool Channel_IsStale(ChannelEngine_t* engine, uint16_t id, uint32_t timeout_ms);

/**
 * @brief The channel holds a real sample: an input once fed (not a seed or
 * the reset 0), a user channel once the engine is armed, anything else always
 */
bool Channel_IsLive(ChannelEngine_t* engine, uint16_t id);

/**
 * @brief Initialize system channels
 */
void Channel_InitSystemChannels(ChannelEngine_t* engine);

/* ============================================================================
 * Peaks
 * ============================================================================ */

/** Peak value for a CH_PEAK_* id (current value while unseeded, 0 if unknown) */
int32_t Channel_GetPeak(ChannelEngine_t* engine, uint16_t peak_id);

/** Restart peaks from the current value: id = CH_PEAK_RESET_ALL, a channel
 *  id or one of its peak ids */
void Channel_ResetPeaks(ChannelEngine_t* engine, uint16_t id);

/** Persistence: peaks of the channel at an engine index. Returns false
 *  (and leaves min/max alone) when the channel has no seeded peak. */
bool Channel_PeakAt(const ChannelEngine_t* engine, uint16_t index,
                    int32_t* min, int32_t* max);
void Channel_PeakRestore(ChannelEngine_t* engine, uint16_t index,
                         int32_t min, int32_t max);

/** Copy min / max into the registered CH_CLASS_PEAK channels (EndTick) */
void Channel_PeakPublish(ChannelEngine_t* engine);

#ifdef __cplusplus
}
#endif

#endif /* CHANNEL_H */
