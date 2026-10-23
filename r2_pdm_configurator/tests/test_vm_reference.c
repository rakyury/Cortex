#include "../vm/reference/vm/vm.h"
#include "../vm/reference/filters/filter.h"
#include "../vm/reference/timers/timer.h"
#include "../vm/reference/tables/table.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* Standalone test oracle; no transport, HAL, output or config-write linkage. */
int main(void)
{
    ChannelEngine_t channels;
    VM_t vm;
    char line[1026];
    Channel_Init(&channels);
    (void)Channel_Register(&channels, 100, CH_TYPE_NUMBER, CH_CLASS_INPUT, "sample");
    Channel_SetValue(&channels, 100, 37);
    VM_Init(&vm, &channels);
    while (fgets(line, sizeof(line), stdin)) {
        uint8_t code[512];
        size_t length = strcspn(line, "\r\n");
        if (strncmp(line, "nodes", 5) == 0) {
            FilterManager_t filters;
            TimerManager_t timers;
            FilterConfig_t sma = Filter_CreateSMA(1000, 100, 3, "sma");
            FilterConfig_t ema = Filter_CreateEMA(1001, 100, 128, "ema");
            FilterConfig_t hyst = Filter_CreateHysteresis(1002, 100, 10, -10, "alarm");
            TimerConfig_t ton = Timer_CreateConfig(1003, TIMER_TON, 100, 100, "ton");
            Table2D_t table = Table_Create2D(1004, 100, 3, "table");
            const int32_t values[] = {-20, -9, 10, 100, 0, -10, INT32_MIN, INT32_MAX};
            const uint32_t ticks[] = {UINT32_MAX - 50, UINT32_MAX - 1, 49, 50, 51, 60, 160, 161};
            Filter_Init(&filters, &channels);
            Timer_Init(&timers, &channels);
            (void)Filter_Add(&filters, &sma);
            (void)Filter_Add(&filters, &ema);
            (void)Filter_Add(&filters, &hyst);
            (void)Timer_Add(&timers, &ton);
            Table_Set2DPoint(&table, 0, INT32_MIN, INT32_MIN);
            Table_Set2DPoint(&table, 1, 0, 100);
            Table_Set2DPoint(&table, 2, INT32_MAX, INT32_MAX);
            for (size_t i = 0; i < sizeof(values) / sizeof(values[0]); ++i) {
                Channel_SetValue(&channels, 100, values[i]);
                Filter_UpdateAll(&filters);
                Timer_UpdateAll(&timers, ticks[i]);
                printf("%" PRId32 ",%" PRId32 ",%" PRId32 ",%d,%" PRId32 "\n",
                       Filter_GetOutput(&filters, 1000), Filter_GetOutput(&filters, 1001),
                       Filter_GetOutput(&filters, 1002), (int)Timer_GetOutput(&timers, 1003),
                       Table_Lookup2D(&table, values[i]));
            }
            continue;
        }
        if (length == 0 || length % 2 != 0 || length > 1024) return 2;
        for (size_t i = 0; i < length / 2; ++i) {
            unsigned value;
            if (sscanf(&line[2 * i], "%2x", &value) != 1) return 3;
            code[i] = (uint8_t)value;
        }
        printf("%" PRId32 "\n", VM_Execute(&vm, code, (uint16_t)(length / 2)));
    }
    return 0;
}
