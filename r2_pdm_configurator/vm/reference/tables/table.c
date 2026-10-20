/**
 * @file table.c
 * @brief 2D and 3D table implementation with interpolation
 */

#include "table.h"
#include <string.h>

/* ============================================================================
 * Initialization
 * ============================================================================ */

void Table_Init(TableManager_t* mgr, ChannelEngine_t* channels)
{
    if (!mgr) return;

    memset(mgr, 0, sizeof(TableManager_t));
    mgr->channels = channels;
    mgr->initialized = true;
}

void Table_Reset(TableManager_t* mgr)
{
    if (!mgr) return;

    mgr->count_2d = 0;
    mgr->count_3d = 0;
}

/* ============================================================================
 * Table Management
 * ============================================================================ */

int Table_Add2D(TableManager_t* mgr, const Table2D_t* table)
{
    if (!mgr || !table || mgr->count_2d >= TABLE_MAX_2D) return -1;

    int idx = mgr->count_2d++;
    memcpy(&mgr->tables_2d[idx], table, sizeof(Table2D_t));
    return idx;
}

int Table_Add3D(TableManager_t* mgr, const Table3D_t* table)
{
    if (!mgr || !table || mgr->count_3d >= TABLE_MAX_3D) return -1;

    int idx = mgr->count_3d++;
    memcpy(&mgr->tables_3d[idx], table, sizeof(Table3D_t));
    return idx;
}

Table2D_t* Table_Get2DById(TableManager_t* mgr, uint16_t id)
{
    if (!mgr) return NULL;
    for (uint16_t i = 0; i < mgr->count_2d; i++) {
        if (mgr->tables_2d[i].id == id) {
            return &mgr->tables_2d[i];
        }
    }
    return NULL;
}

Table3D_t* Table_Get3DById(TableManager_t* mgr, uint16_t id)
{
    if (!mgr) return NULL;
    for (uint16_t i = 0; i < mgr->count_3d; i++) {
        if (mgr->tables_3d[i].id == id) {
            return &mgr->tables_3d[i];
        }
    }
    return NULL;
}

/* ============================================================================
 * Linear Interpolation Helper
 * ============================================================================ */

static int32_t lerp(int32_t x, int32_t x0, int32_t x1, int32_t y0, int32_t y1)
{
    if (x1 == x0) return y0;

    /* Differences in 64 bits: y1 - y0 and x - x0 overflowed int32 (UB, and
     * a wrong sign) for tables spanning more than 2^31 (values near the
     * int32 limits, -2e9..2e9 axes). The product of two differences >= 2^31
     * would overflow int64 too: drop low bits of the x ratio until it fits.
     * The result saturates to int32 (unsorted axes can extrapolate). */
    int64_t dy = (int64_t)y1 - y0;
    int64_t dx = (int64_t)x - x0;
    int64_t span = (int64_t)x1 - x0;
    while ((dy >= 0x80000000LL || dy <= -0x80000000LL) &&
           (dx >= 0x80000000LL || dx <= -0x80000000LL ||
            span >= 0x80000000LL || span <= -0x80000000LL)) {
        dx /= 2;
        span /= 2;
    }
    if (span == 0) return y0;
    int64_t v = (int64_t)y0 + dy * dx / span;
    if (v > INT32_MAX) return INT32_MAX;
    if (v < INT32_MIN) return INT32_MIN;
    return (int32_t)v;
}

/* ============================================================================
 * 2D Table Lookup
 * ============================================================================ */

int32_t Table_Lookup2D(Table2D_t* table, int32_t x)
{
    if (!table || table->axis_size == 0) return 0;

    uint8_t size = table->axis_size;

    if (table->flags & TABLE_FLAG_CLAMP) {
        if (x <= table->x_axis[0]) return table->values[0];
        if (x >= table->x_axis[size - 1]) return table->values[size - 1];
    } else {
        if (x <= table->x_axis[0]) return table->values[0];
        if (x >= table->x_axis[size - 1]) return table->values[size - 1];
    }

    uint8_t i = 0;
    while (i < size - 1 && table->x_axis[i + 1] < x) {
        i++;
    }

    if (table->interp == TABLE_INTERP_LINEAR && i < size - 1) {
        return lerp(x,
                    table->x_axis[i], table->x_axis[i + 1],
                    table->values[i], table->values[i + 1]);
    }

    if (i < size - 1) {
        int32_t mid = (table->x_axis[i] + table->x_axis[i + 1]) / 2;
        return (x < mid) ? table->values[i] : table->values[i + 1];
    }

    return table->values[i];
}

/* ============================================================================
 * 3D Table Lookup (Bilinear Interpolation)
 * ============================================================================ */

int32_t Table_Lookup3D(Table3D_t* table, int32_t x, int32_t y)
{
    if (!table || table->x_size == 0 || table->y_size == 0) return 0;

    uint8_t x_size = table->x_size;
    uint8_t y_size = table->y_size;

    if (x <= table->x_axis[0]) x = table->x_axis[0];
    else if (x >= table->x_axis[x_size - 1]) x = table->x_axis[x_size - 1];

    if (y <= table->y_axis[0]) y = table->y_axis[0];
    else if (y >= table->y_axis[y_size - 1]) y = table->y_axis[y_size - 1];

    uint8_t xi = 0;
    while (xi < x_size - 1 && table->x_axis[xi + 1] < x) xi++;

    uint8_t yi = 0;
    while (yi < y_size - 1 && table->y_axis[yi + 1] < y) yi++;

    #define VAL(xidx, yidx) table->values[(yidx) * x_size + (xidx)]

    int32_t v00 = VAL(xi, yi);

    if (table->interp != TABLE_INTERP_LINEAR) {
        return v00;
    }

    if (xi >= x_size - 1 && yi >= y_size - 1) {
        return v00;
    }

    int32_t x0 = table->x_axis[xi];
    int32_t x1 = (xi < x_size - 1) ? table->x_axis[xi + 1] : x0;
    int32_t y0 = table->y_axis[yi];
    int32_t y1 = (yi < y_size - 1) ? table->y_axis[yi + 1] : y0;

    int32_t v01 = (xi < x_size - 1) ? VAL(xi + 1, yi) : v00;
    int32_t v10 = (yi < y_size - 1) ? VAL(xi, yi + 1) : v00;
    int32_t v11 = (xi < x_size - 1 && yi < y_size - 1) ? VAL(xi + 1, yi + 1) :
                  (xi < x_size - 1) ? v01 :
                  (yi < y_size - 1) ? v10 : v00;

    #undef VAL

    int32_t v_y0 = lerp(x, x0, x1, v00, v01);
    int32_t v_y1 = lerp(x, x0, x1, v10, v11);

    return lerp(y, y0, y1, v_y0, v_y1);
}

/* ============================================================================
 * Update All Tables
 * ============================================================================ */

void Table_UpdateAll(TableManager_t* mgr)
{
    if (!mgr || !mgr->channels) return;

    for (uint16_t i = 0; i < mgr->count_2d; i++) {
        Table2D_t* table = &mgr->tables_2d[i];
        if (!(table->flags & TABLE_FLAG_ENABLED)) continue;

        int32_t x = Channel_GetValue(mgr->channels, table->input_channel);
        int32_t y = Table_Lookup2D(table, x);
        Channel_SetValue(mgr->channels, table->id, y);
    }

    for (uint16_t i = 0; i < mgr->count_3d; i++) {
        Table3D_t* table = &mgr->tables_3d[i];
        if (!(table->flags & TABLE_FLAG_ENABLED)) continue;

        int32_t x = Channel_GetValue(mgr->channels, table->x_channel);
        int32_t y = Channel_GetValue(mgr->channels, table->y_channel);
        int32_t z = Table_Lookup3D(table, x, y);
        Channel_SetValue(mgr->channels, table->id, z);
    }
}

/* ============================================================================
 * Construction Helpers
 * ============================================================================ */

Table2D_t Table_Create2D(uint16_t id, uint16_t input_ch,
                          uint8_t axis_size, const char* name)
{
    Table2D_t table = {0};
    table.id = id;
    table.input_channel = input_ch;
    table.axis_size = (axis_size > TABLE_MAX_AXIS_SIZE) ?
                       TABLE_MAX_AXIS_SIZE : axis_size;
    table.interp = TABLE_INTERP_LINEAR;
    table.flags = TABLE_FLAG_ENABLED | TABLE_FLAG_CLAMP;
    if (name) strncpy(table.name, name, sizeof(table.name) - 1);
    return table;
}

void Table_Set2DPoint(Table2D_t* table, uint8_t idx, int32_t x, int32_t y)
{
    if (!table || idx >= TABLE_MAX_AXIS_SIZE) return;
    table->x_axis[idx] = x;
    table->values[idx] = y;
}

Table3D_t Table_Create3D(uint16_t id, uint16_t x_ch, uint16_t y_ch,
                          uint8_t x_size, uint8_t y_size, const char* name)
{
    Table3D_t table = {0};
    table.id = id;
    table.x_channel = x_ch;
    table.y_channel = y_ch;
    table.x_size = (x_size > TABLE_MAX_AXIS_SIZE) ? TABLE_MAX_AXIS_SIZE : x_size;
    table.y_size = (y_size > TABLE_MAX_AXIS_SIZE) ? TABLE_MAX_AXIS_SIZE : y_size;
    table.interp = TABLE_INTERP_LINEAR;
    table.flags = TABLE_FLAG_ENABLED | TABLE_FLAG_CLAMP;
    if (name) strncpy(table.name, name, sizeof(table.name) - 1);
    return table;
}

void Table_Set3DAxes(Table3D_t* table,
                      const int32_t* x_axis, uint8_t x_size,
                      const int32_t* y_axis, uint8_t y_size)
{
    if (!table) return;

    if (x_axis && x_size > 0) {
        uint8_t n = (x_size > TABLE_MAX_AXIS_SIZE) ? TABLE_MAX_AXIS_SIZE : x_size;
        memcpy(table->x_axis, x_axis, n * sizeof(int32_t));
        table->x_size = n;
    }

    if (y_axis && y_size > 0) {
        uint8_t n = (y_size > TABLE_MAX_AXIS_SIZE) ? TABLE_MAX_AXIS_SIZE : y_size;
        memcpy(table->y_axis, y_axis, n * sizeof(int32_t));
        table->y_size = n;
    }
}

void Table_Set3DValue(Table3D_t* table, uint8_t x_idx, uint8_t y_idx,
                       int32_t value)
{
    if (!table) return;
    if (x_idx >= table->x_size || y_idx >= table->y_size) return;

    table->values[y_idx * table->x_size + x_idx] = value;
}
