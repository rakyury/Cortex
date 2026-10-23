/**
 * @file channel.c
 * @brief Channel Engine implementation
 */

#include "channel.h"
#include <string.h>

/* ============================================================================
 * Initialization
 * ============================================================================ */

void Channel_Init(ChannelEngine_t* engine)
{
    if (!engine) return;

    memset(engine, 0, sizeof(ChannelEngine_t));
    engine->initialized = true;

    Channel_InitSystemChannels(engine);
}

void Channel_Reset(ChannelEngine_t* engine)
{
    if (!engine) return;

    for (uint16_t i = 0; i < engine->channel_count; i++) {
        engine->channels[i].value = 0;
        engine->channels[i].prev_value = 0;
        engine->channels[i].flags = 0;
        engine->channels[i].updated_tick = 0;
    }
    engine->peak_armed = false;
    engine->seed_count = 0;
    engine->peak_seq++;
}

void Channel_InitSystemChannels(ChannelEngine_t* engine)
{
    if (!engine) return;

    Channel_Register(engine, CH_SYS_ZERO, CH_TYPE_NUMBER, CH_CLASS_SYSTEM, "zero");
    Channel_Register(engine, CH_SYS_ONE, CH_TYPE_NUMBER, CH_CLASS_SYSTEM, "one");
    Channel_SetValue(engine, CH_SYS_ONE, 1);

    Channel_Register(engine, CH_SYS_TIMEBASE_MS, CH_TYPE_NUMBER, CH_CLASS_SYSTEM, "timebase_ms");
    Channel_Register(engine, CH_SYS_DT_MS, CH_TYPE_NUMBER, CH_CLASS_SYSTEM, "dt_ms");
    Channel_Register(engine, CH_SYS_CURRENT_SCREEN, CH_TYPE_NUMBER, CH_CLASS_SYSTEM, "current_screen");
    Channel_Register(engine, CH_SYS_BRIGHTNESS, CH_TYPE_NUMBER, CH_CLASS_SYSTEM, "brightness");
    Channel_SetValue(engine, CH_SYS_BRIGHTNESS, 100);

    Channel_Register(engine, CH_SYS_WARNING_STATE, CH_TYPE_BOOL, CH_CLASS_SYSTEM, "warning_state");
    Channel_Register(engine, CH_SYS_FAULT_STATE, CH_TYPE_BOOL, CH_CLASS_SYSTEM, "fault_state");
}

/* ============================================================================
 * Channel Registration
 * ============================================================================ */

Channel_t* Channel_Register(ChannelEngine_t* engine, uint16_t id,
                            ChannelType_t type, ChannelClass_t ch_class,
                            const char* name)
{
    if (!engine || engine->channel_count >= MAX_CHANNELS) return NULL;

    Channel_t* existing = Channel_GetById(engine, id);
    if (existing) return existing;

    Channel_t* ch = &engine->channels[engine->channel_count++];
    memset(ch, 0, sizeof(Channel_t));

    ch->id = id;
    ch->type = (uint8_t)type;
    ch->ch_class = (uint8_t)ch_class;
    ch->flags = CH_FLAG_VALID;
    ch->peak_src = CH_PEAK_SRC_NONE;
    if (ch_class == CH_CLASS_PEAK) engine->peak_channels++;

    if (name) {
        strncpy(ch->name, name, MAX_CHANNEL_NAME - 1);
        ch->name[MAX_CHANNEL_NAME - 1] = '\0';
    }

    return ch;
}

/* ============================================================================
 * Channel Lookup
 * ============================================================================ */

Channel_t* Channel_GetById(ChannelEngine_t* engine, uint16_t id)
{
    if (!engine) return NULL;

    for (uint16_t i = 0; i < engine->channel_count; i++) {
        if (engine->channels[i].id == id) {
            return &engine->channels[i];
        }
    }
    return NULL;
}

int32_t Channel_GetValue(ChannelEngine_t* engine, uint16_t id)
{
    Channel_t* ch = Channel_GetById(engine, id);
    /* a registered readout answers live too (its mirror lags to EndTick) */
    if (ch && ch->ch_class != CH_CLASS_PEAK) return ch->value;
    return CH_IS_PEAK_ID(id) ? Channel_GetPeak(engine, id) : (ch ? ch->value : 0);
}

/* ============================================================================
 * Peaks
 * ============================================================================ */

/* One sample into a channel's min / max; seeds an unseeded channel */
static void peak_sample(ChannelEngine_t* engine, Channel_t* ch)
{
    int32_t v = ch->value;
    if (!(ch->flags & CH_FLAG_PEAK_SEEDED)) {
        ch->peak_min = ch->peak_max = v;
        ch->flags |= CH_FLAG_PEAK_SEEDED;
        engine->peak_seq++;
    } else if (v > ch->peak_max) {
        ch->peak_max = v;
        engine->peak_seq++;
    } else if (v < ch->peak_min) {
        ch->peak_min = v;
        engine->peak_seq++;
    }
}

/* User channels track once an input is live and no input still holds a
 * design seed (a math channel over a seed would latch a made-up peak) */
static bool user_peaks_live(const ChannelEngine_t* engine)
{
    return engine->peak_armed && engine->seed_count == 0;
}

/* Sample when the channel is live: inputs after their first value, user
 * channels once the engine is armed; system / peak channels never */
static void peak_track(ChannelEngine_t* engine, Channel_t* ch)
{
    if (ch->ch_class == CH_CLASS_INPUT) {
        if (ch->flags & CH_FLAG_FED) peak_sample(engine, ch);
    } else if (ch->ch_class == CH_CLASS_USER) {
        if (user_peaks_live(engine)) peak_sample(engine, ch);
    }
}

/* A real sample, not the reset 0 / a design seed: inputs once fed, user
 * channels once any input is live (peak_armed; their peaks also wait for the
 * last seed, which the M4 drops within its grace - the preview engine never
 * does, and a min / max over a math channel would never start there),
 * anything else (system constants, peak readouts, an unknown id) always */
bool Channel_IsLive(ChannelEngine_t* engine, uint16_t id)
{
    if (!engine) return false;
    Channel_t* ch = Channel_GetById(engine, id);
    if (!ch) return true;
    if (ch->ch_class == CH_CLASS_INPUT) return (ch->flags & CH_FLAG_FED) != 0;
    if (ch->ch_class == CH_CLASS_USER) return engine->peak_armed;
    return true;
}

static int32_t peak_of(const Channel_t* src, bool want_min)
{
    if (!(src->flags & CH_FLAG_PEAK_SEEDED)) return src->value;
    return want_min ? src->peak_min : src->peak_max;
}

int32_t Channel_GetPeak(ChannelEngine_t* engine, uint16_t peak_id)
{
    if (!engine || !CH_IS_PEAK_ID(peak_id)) return 0;
    Channel_t* src = Channel_GetById(engine, CH_PEAK_SOURCE(peak_id));
    return src ? peak_of(src, CH_PEAK_IS_MIN(peak_id)) : 0;
}

void Channel_ResetPeaks(ChannelEngine_t* engine, uint16_t id)
{
    if (!engine) return;
    if (CH_IS_PEAK_ID(id)) id = CH_PEAK_SOURCE(id);
    for (uint16_t i = 0; i < engine->channel_count; i++) {
        Channel_t* ch = &engine->channels[i];
        if (id != CH_PEAK_RESET_ALL && ch->id != id) continue;
        ch->flags &= (uint8_t)~CH_FLAG_PEAK_SEEDED;
        peak_track(engine, ch);         /* restart from the current value */
    }
    engine->peak_seq++;
    if (engine->peak_channels) {
        /* the readouts follow now, not one tick later */
        Channel_PeakPublish(engine);
    }
}

bool Channel_PeakAt(const ChannelEngine_t* engine, uint16_t index,
                    int32_t* min, int32_t* max)
{
    if (!engine || index >= engine->channel_count) return false;
    const Channel_t* ch = &engine->channels[index];
    if (!(ch->flags & CH_FLAG_PEAK_SEEDED)) return false;
    if (min) *min = ch->peak_min;
    if (max) *max = ch->peak_max;
    return true;
}

void Channel_PeakRestore(ChannelEngine_t* engine, uint16_t index,
                         int32_t min, int32_t max)
{
    if (!engine || index >= engine->channel_count || min > max) return;
    Channel_t* ch = &engine->channels[index];
    if (ch->ch_class != CH_CLASS_INPUT && ch->ch_class != CH_CLASS_USER) return;
    ch->peak_min = min;
    ch->peak_max = max;
    ch->flags |= CH_FLAG_PEAK_SEEDED;
    engine->peak_seq++;
}

/* Registered peak channels mirror their source's min / max */
void Channel_PeakPublish(ChannelEngine_t* engine)
{
    for (uint16_t i = 0; i < engine->channel_count; i++) {
        Channel_t* ch = &engine->channels[i];
        if (ch->ch_class != CH_CLASS_PEAK) continue;
        if (ch->peak_src == CH_PEAK_SRC_NONE) {
            ch->peak_src = CH_PEAK_SRC_MISSING;
            uint16_t sid = CH_PEAK_SOURCE(ch->id);
            for (uint16_t k = 0; CH_IS_PEAK_ID(ch->id) && k < engine->channel_count; k++)
                if (engine->channels[k].id == sid) { ch->peak_src = k; break; }
        }
        if (ch->peak_src >= engine->channel_count) continue;
        int32_t v = peak_of(&engine->channels[ch->peak_src], CH_PEAK_IS_MIN(ch->id));
        if (v != ch->value) ch->flags |= CH_FLAG_CHANGED;
        ch->value = v;
        ch->updated_tick = engine->current_tick;
    }
}

/* ============================================================================
 * Channel Value Updates
 * ============================================================================ */

/* Shared by both setters: peak channels are engine-owned (a host mirror
 * writing them is ignored); an input sample feeds its peaks right away so
 * a spike between two engine ticks is not lost. */
static void set_value(ChannelEngine_t* engine, Channel_t* ch, int32_t value,
                      uint32_t tick)
{
    if (ch->ch_class == CH_CLASS_PEAK) return;
    if (ch->flags & CH_FLAG_SEED) {          /* the first real value replaces the seed */
        ch->flags &= (uint8_t)~CH_FLAG_SEED;
        if (engine->seed_count) engine->seed_count--;
    }
    if (ch->value != value) {
        ch->flags |= CH_FLAG_CHANGED;
    }
    ch->value = value;
    ch->updated_tick = tick;
    if (ch->ch_class == CH_CLASS_INPUT) {
        ch->flags |= CH_FLAG_FED;
        engine->peak_armed = true;
        peak_sample(engine, ch);
    }
}

void Channel_SetValue(ChannelEngine_t* engine, uint16_t id, int32_t value)
{
    Channel_t* ch = Channel_GetById(engine, id);
    if (!ch) return;
    set_value(engine, ch, value, engine->current_tick);
}

void Channel_SetValueWithTick(ChannelEngine_t* engine, uint16_t id,
                               int32_t value, uint32_t tick)
{
    Channel_t* ch = Channel_GetById(engine, id);
    if (!ch) return;
    set_value(engine, ch, value, tick);
}

/* ============================================================================
 * Seeds (design preview values until the first real sample)
 * ============================================================================ */

bool Channel_Seed(ChannelEngine_t* engine, uint16_t id, int32_t value)
{
    Channel_t* ch = Channel_GetById(engine, id);
    if (!ch || ch->ch_class != CH_CLASS_INPUT || (ch->flags & CH_FLAG_FED)) return false;
    if (ch->value != value) ch->flags |= CH_FLAG_CHANGED;
    ch->value = value;
    ch->prev_value = value;                  /* no edge out of a seed */
    if (!(ch->flags & CH_FLAG_SEED)) {
        ch->flags |= CH_FLAG_SEED;
        engine->seed_count++;
    }
    return true;
}

uint16_t Channel_DropSeeds(ChannelEngine_t* engine)
{
    uint16_t n = 0;
    if (!engine) return 0;
    for (uint16_t i = 0; i < engine->channel_count; i++) {
        Channel_t* ch = &engine->channels[i];
        if (!(ch->flags & CH_FLAG_SEED)) continue;
        ch->flags &= (uint8_t)~CH_FLAG_SEED;
        if (ch->value != 0) ch->flags |= CH_FLAG_CHANGED;
        ch->value = 0;
        n++;
    }
    engine->seed_count = 0;
    return n;
}

bool Channel_IsSeeded(ChannelEngine_t* engine, uint16_t id)
{
    Channel_t* ch = Channel_GetById(engine, id);
    return ch ? (ch->flags & CH_FLAG_SEED) != 0 : false;
}

/* ============================================================================
 * Edge Detection
 * ============================================================================ */

bool Channel_HasChanged(ChannelEngine_t* engine, uint16_t id)
{
    Channel_t* ch = Channel_GetById(engine, id);
    return ch ? (ch->flags & CH_FLAG_CHANGED) != 0 : false;
}

bool Channel_RisingEdge(ChannelEngine_t* engine, uint16_t id)
{
    Channel_t* ch = Channel_GetById(engine, id);
    return ch ? (ch->flags & CH_FLAG_RISING) != 0 : false;
}

bool Channel_FallingEdge(ChannelEngine_t* engine, uint16_t id)
{
    Channel_t* ch = Channel_GetById(engine, id);
    return ch ? (ch->flags & CH_FLAG_FALLING) != 0 : false;
}

/* ============================================================================
 * Tick Management
 * ============================================================================ */

void Channel_BeginTick(ChannelEngine_t* engine, uint32_t tick_ms)
{
    if (!engine) return;

    engine->dt_ms = tick_ms - engine->current_tick;
    engine->current_tick = tick_ms;

    Channel_SetValue(engine, CH_SYS_TIMEBASE_MS, (int32_t)tick_ms);
    Channel_SetValue(engine, CH_SYS_DT_MS, (int32_t)engine->dt_ms);

    for (uint16_t i = 0; i < engine->channel_count; i++) {
        Channel_t* ch = &engine->channels[i];
        ch->prev_value = ch->value;
        ch->flags &= ~(CH_FLAG_CHANGED | CH_FLAG_RISING | CH_FLAG_FALLING);
    }
}

void Channel_EndTick(ChannelEngine_t* engine)
{
    if (!engine) return;

    /* a design without inputs: user channels track from the first tick */
    if (!engine->peak_armed) {
        bool has_input = false;
        for (uint16_t i = 0; i < engine->channel_count && !has_input; i++)
            has_input = engine->channels[i].ch_class == CH_CLASS_INPUT;
        engine->peak_armed = !has_input;
    }

    for (uint16_t i = 0; i < engine->channel_count; i++) {
        Channel_t* ch = &engine->channels[i];
        if (ch->ch_class == CH_CLASS_USER && user_peaks_live(engine)) peak_sample(engine, ch);

        if (ch->type == CH_TYPE_BOOL || ch->type == CH_TYPE_NUMSTATE) {
            bool was_true = ch->prev_value != 0;
            bool is_true = ch->value != 0;

            if (!was_true && is_true) {
                ch->flags |= CH_FLAG_RISING;
            }
            if (was_true && !is_true) {
                ch->flags |= CH_FLAG_FALLING;
            }
        }
    }
    if (engine->peak_channels) Channel_PeakPublish(engine);
}

/* ============================================================================
 * Stale Detection
 * ============================================================================ */

bool Channel_IsStale(ChannelEngine_t* engine, uint16_t id, uint32_t timeout_ms)
{
    Channel_t* ch = Channel_GetById(engine, id);
    if (!ch) return true;

    if (ch->ch_class == CH_CLASS_SYSTEM) return false;

    uint32_t age = engine->current_tick - ch->updated_tick;
    bool stale = age > timeout_ms;

    if (stale) {
        ch->flags |= CH_FLAG_STALE;
    } else {
        ch->flags &= ~CH_FLAG_STALE;
    }

    return stale;
}
