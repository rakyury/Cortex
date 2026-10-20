/**
 * @file table.h
 * @brief 2D and 3D lookup tables with interpolation
 *
 * Tables map input values to output values:
 * - Table2D: y = f(x) - single input
 * - Table3D: z = f(x, y) - two inputs (like fuel/ignition maps)
 */

#ifndef TABLE_H
#define TABLE_H

#include <stdint.h>
#include <stdbool.h>
#include "../channel_engine/channel.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Table Configuration
 * ============================================================================ */

#define TABLE_MAX_AXIS_SIZE 16
#ifndef TABLE_MAX_2D
#define TABLE_MAX_2D 8 /* STM32F446 profile; Racing Dash default is larger */
#endif
#ifndef TABLE_MAX_3D
#define TABLE_MAX_3D 4 /* STM32F446 profile; Racing Dash default is larger */
#endif

typedef enum {
    TABLE_INTERP_NONE   = 0,    /* Nearest value */
    TABLE_INTERP_LINEAR = 1,    /* Linear interpolation */
} TableInterp_t;

/* 2D Table: y = f(x) */
typedef struct {
    uint16_t id;                /* Table ID / output channel ID */
    uint8_t  axis_size;         /* Number of axis points */
    uint8_t  interp;            /* TableInterp_t */
    uint8_t  flags;
    uint8_t  reserved;

    uint16_t input_channel;     /* X input channel */
    uint16_t reserved2;

    int32_t  x_axis[TABLE_MAX_AXIS_SIZE];   /* X axis values */
    int32_t  values[TABLE_MAX_AXIS_SIZE];   /* Y output values */

    char name[24];
} Table2D_t;

/* 3D Table: z = f(x, y) */
typedef struct {
    uint16_t id;                /* Table ID / output channel ID */
    uint8_t  x_size;            /* X axis size */
    uint8_t  y_size;            /* Y axis size */
    uint8_t  interp;            /* TableInterp_t */
    uint8_t  flags;

    uint16_t x_channel;         /* X input channel */
    uint16_t y_channel;         /* Y input channel */

    int32_t  x_axis[TABLE_MAX_AXIS_SIZE];   /* X axis values */
    int32_t  y_axis[TABLE_MAX_AXIS_SIZE];   /* Y axis values */
    int32_t  values[TABLE_MAX_AXIS_SIZE * TABLE_MAX_AXIS_SIZE]; /* Z values */

    char name[24];
} Table3D_t;

/* Table flags */
#define TABLE_FLAG_ENABLED  (1 << 0)
#define TABLE_FLAG_CLAMP    (1 << 1)    /* Clamp to axis bounds */

/* ============================================================================
 * Table Manager
 * ============================================================================ */

typedef struct {
    Table2D_t tables_2d[TABLE_MAX_2D];
    uint16_t  count_2d;

    Table3D_t tables_3d[TABLE_MAX_3D];
    uint16_t  count_3d;

    ChannelEngine_t* channels;

    bool initialized;
} TableManager_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Initialize table manager
 */
void Table_Init(TableManager_t* mgr, ChannelEngine_t* channels);

/**
 * @brief Reset all tables
 */
void Table_Reset(TableManager_t* mgr);

/**
 * @brief Add 2D table
 * @return Table index or -1 on error
 */
int Table_Add2D(TableManager_t* mgr, const Table2D_t* table);

/**
 * @brief Add 3D table
 * @return Table index or -1 on error
 */
int Table_Add3D(TableManager_t* mgr, const Table3D_t* table);

/**
 * @brief Update all tables (compute outputs)
 */
void Table_UpdateAll(TableManager_t* mgr);

/**
 * @brief Lookup 2D table directly (without channel)
 */
int32_t Table_Lookup2D(Table2D_t* table, int32_t x);

/**
 * @brief Lookup 3D table directly (without channel)
 */
int32_t Table_Lookup3D(Table3D_t* table, int32_t x, int32_t y);

/**
 * @brief Get 2D table by ID
 */
Table2D_t* Table_Get2DById(TableManager_t* mgr, uint16_t id);

/**
 * @brief Get 3D table by ID
 */
Table3D_t* Table_Get3DById(TableManager_t* mgr, uint16_t id);

/* ============================================================================
 * Table Construction Helpers
 * ============================================================================ */

/**
 * @brief Create empty 2D table
 */
Table2D_t Table_Create2D(uint16_t id, uint16_t input_ch,
                          uint8_t axis_size, const char* name);

/**
 * @brief Set 2D table axis point
 */
void Table_Set2DPoint(Table2D_t* table, uint8_t idx,
                       int32_t x, int32_t y);

/**
 * @brief Create empty 3D table
 */
Table3D_t Table_Create3D(uint16_t id, uint16_t x_ch, uint16_t y_ch,
                          uint8_t x_size, uint8_t y_size, const char* name);

/**
 * @brief Set 3D table axes
 */
void Table_Set3DAxes(Table3D_t* table,
                      const int32_t* x_axis, uint8_t x_size,
                      const int32_t* y_axis, uint8_t y_size);

/**
 * @brief Set 3D table value at x,y index
 */
void Table_Set3DValue(Table3D_t* table, uint8_t x_idx, uint8_t y_idx,
                       int32_t value);

#ifdef __cplusplus
}
#endif

#endif /* TABLE_H */
