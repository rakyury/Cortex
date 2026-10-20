/**
 * @file vm.c
 * @brief Bytecode Virtual Machine implementation
 */

#include "vm.h"
#include <string.h>

/* int32 arithmetic with two's-complement wrap-around, defined on every
 * target. Plain signed + - * overflow is undefined behaviour in C (the
 * compiler may assume it never happens), and INT32_MIN / -1 raises a
 * division exception on x86 - the configurator preview runs this VM there
 * and a formula like `a / b` crashed the whole app for that input pair. */
static inline int32_t wrap32(uint32_t v) { return (int32_t)v; }
#define VM_ADD(a, b) wrap32((uint32_t)(a) + (uint32_t)(b))
#define VM_SUB(a, b) wrap32((uint32_t)(a) - (uint32_t)(b))
#define VM_MUL(a, b) wrap32((uint32_t)(a) * (uint32_t)(b))
#define VM_NEG(a)    wrap32(0u - (uint32_t)(a))

/* dx * dy / span for differences of int32 values (up to 33 bits each):
 * the product can pass int64, so while both factors are that wide the
 * ratio dx / span drops a low bit (result exact to well under 1 ppm) */
static int64_t ratio_mul(int64_t dx, int64_t dy, int64_t span)
{
    while ((dy >= 0x80000000LL || dy <= -0x80000000LL) &&
           (dx >= 0x80000000LL || dx <= -0x80000000LL ||
            span >= 0x80000000LL || span <= -0x80000000LL)) {
        dx /= 2;
        span /= 2;
    }
    return span ? dx * dy / span : 0;
}

/* ============================================================================
 * Initialization
 * ============================================================================ */

void VM_Init(VM_t* vm, ChannelEngine_t* channels)
{
    if (!vm) return;

    memset(vm, 0, sizeof(VM_t));
    vm->channels = channels;
    vm->initialized = true;
}

void VM_Reset(VM_t* vm)
{
    if (!vm) return;

    vm->bytecode_used = 0;
    vm->program_count = 0;
    vm->exec_count = 0;
    vm->sp = 0;
    vm->total_ops = 0;
    vm->total_programs = 0;
}

/* ============================================================================
 * Program Management
 * ============================================================================ */

int VM_AddProgram(VM_t* vm, uint16_t channel_id,
                  const uint8_t* bytecode, uint16_t length)
{
    if (!vm || !bytecode || length == 0) return -1;
    if (vm->program_count >= VM_MAX_PROGRAMS) return -1;
    if (vm->bytecode_used + length > VM_MAX_BYTECODE) return -1;

    VMProgram_t* prog = &vm->programs[vm->program_count];
    prog->channel_id = channel_id;
    prog->offset = vm->bytecode_used;
    prog->length = length;
    prog->priority = 0;
    prog->flags = VM_PROG_ENABLED | VM_PROG_DIRTY;

    memcpy(&vm->bytecode[vm->bytecode_used], bytecode, length);
    vm->bytecode_used += length;

    return vm->program_count++;
}

void VM_RemoveProgram(VM_t* vm, uint16_t channel_id)
{
    if (!vm) return;

    for (uint16_t i = 0; i < vm->program_count; i++) {
        if (vm->programs[i].channel_id == channel_id) {
            vm->programs[i].flags &= ~VM_PROG_ENABLED;
            break;
        }
    }
}

/* ============================================================================
 * Dependency Analysis & Topological Sort
 * ============================================================================ */

static bool program_depends_on(VM_t* vm, uint8_t prog_a, uint8_t prog_b)
{
    VMProgram_t* pa = &vm->programs[prog_a];
    uint16_t target_ch = vm->programs[prog_b].channel_id;

    const uint8_t* code = &vm->bytecode[pa->offset];
    uint16_t len = pa->length;

    for (uint16_t i = 0; i < len; ) {
        uint8_t op = code[i++];

        if (op == OP_PUSH_CH && i + 1 < len) {
            uint16_t ch_id = code[i] | (code[i + 1] << 8);
            if (ch_id == target_ch) return true;
            i += 2;
        }
        else if (op == OP_PUSH_CONST) i += 4;
        else if (op == OP_STORE_CH) i += 2;
        else if (op == OP_LOAD_IDX || op == OP_STORE_IDX) i += 2;
        else if (op == OP_TABLE_2D || op == OP_TABLE_3D) i += 2;
        else if (op == OP_TIMER_VAL || op == OP_FILTER_VAL) i += 2;
        else if (op == OP_SWITCH) {
            if (i < len) {
                uint8_t count = code[i++];
                i += count * 4;
            }
        }
    }

    return false;
}

void VM_BuildExecutionOrder(VM_t* vm)
{
    if (!vm) return;

    vm->exec_count = 0;

    uint8_t in_degree[VM_MAX_PROGRAMS] = {0};
    bool processed[VM_MAX_PROGRAMS] = {false};

    for (uint8_t i = 0; i < vm->program_count; i++) {
        if (!(vm->programs[i].flags & VM_PROG_ENABLED)) continue;

        for (uint8_t j = 0; j < vm->program_count; j++) {
            if (i != j && (vm->programs[j].flags & VM_PROG_ENABLED)) {
                if (program_depends_on(vm, i, j)) {
                    in_degree[i]++;
                }
            }
        }
    }

    bool changed = true;
    while (changed && vm->exec_count < vm->program_count) {
        changed = false;

        for (uint8_t i = 0; i < vm->program_count; i++) {
            if (processed[i]) continue;
            if (!(vm->programs[i].flags & VM_PROG_ENABLED)) continue;

            if (in_degree[i] == 0) {
                vm->exec_order[vm->exec_count++] = i;
                vm->programs[i].priority = vm->exec_count;
                processed[i] = true;

                for (uint8_t j = 0; j < vm->program_count; j++) {
                    if (!processed[j] && (vm->programs[j].flags & VM_PROG_ENABLED)) {
                        if (program_depends_on(vm, j, i)) {
                            if (in_degree[j] > 0) in_degree[j]--;
                        }
                    }
                }

                changed = true;
            }
        }
    }
}

/* ============================================================================
 * Execution
 * ============================================================================ */

void VM_ExecuteAll(VM_t* vm)
{
    if (!vm) return;

    for (uint8_t i = 0; i < vm->exec_count; i++) {
        uint8_t prog_idx = vm->exec_order[i];
        VM_ExecuteProgram(vm, prog_idx);
        vm->total_programs++;
    }
}

int32_t VM_ExecuteProgram(VM_t* vm, uint8_t prog_idx)
{
    if (!vm || prog_idx >= vm->program_count) return 0;

    VMProgram_t* prog = &vm->programs[prog_idx];
    if (!(prog->flags & VM_PROG_ENABLED)) return 0;

    const uint8_t* code = &vm->bytecode[prog->offset];
    int32_t result = VM_Execute(vm, code, prog->length);

    Channel_SetValue(vm->channels, prog->channel_id, result);

    return result;
}

int32_t VM_Execute(VM_t* vm, const uint8_t* code, uint16_t length)
{
    if (!vm || !code) return 0;

    vm->sp = 0;
    uint16_t pc = 0;

    while (pc < length) {
        uint8_t op = code[pc++];
        vm->total_ops++;

        int32_t a, b, c;
        uint16_t id;

        switch (op) {
            case OP_NOP:
                break;

            case OP_PUSH_CONST:
                if (pc + 4 <= length) {
                    a = wrap32((uint32_t)code[pc] | ((uint32_t)code[pc+1] << 8) |
                               ((uint32_t)code[pc+2] << 16) | ((uint32_t)code[pc+3] << 24));
                    VM_Push(vm, a);
                    pc += 4;
                }
                break;

            case OP_PUSH_CH:
                if (pc + 2 <= length) {
                    id = code[pc] | (code[pc + 1] << 8);
                    VM_Push(vm, Channel_GetValue(vm->channels, id));
                    pc += 2;
                }
                break;

            case OP_POP:
                VM_Pop(vm);
                break;

            case OP_DUP:
                VM_Push(vm, VM_Peek(vm));
                break;

            case OP_SWAP:
                if (vm->sp >= 2) {
                    a = vm->stack[vm->sp - 1];
                    vm->stack[vm->sp - 1] = vm->stack[vm->sp - 2];
                    vm->stack[vm->sp - 2] = a;
                }
                break;

            case OP_ROT:
                if (vm->sp >= 3) {
                    a = vm->stack[vm->sp - 3];
                    vm->stack[vm->sp - 3] = vm->stack[vm->sp - 2];
                    vm->stack[vm->sp - 2] = vm->stack[vm->sp - 1];
                    vm->stack[vm->sp - 1] = a;
                }
                break;

            /* Arithmetic */
            case OP_ADD:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, VM_ADD(a, b));
                break;

            case OP_SUB:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, VM_SUB(a, b));
                break;

            case OP_MUL:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, VM_MUL(a, b));
                break;

            case OP_DIV:
                b = VM_Pop(vm); a = VM_Pop(vm);
                /* x / -1 = -x (wraps for INT32_MIN instead of trapping) */
                VM_Push(vm, (b == 0) ? 0 : (b == -1) ? VM_NEG(a) : (a / b));
                break;

            case OP_MOD:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (b == 0 || b == -1) ? 0 : (a % b));
                break;

            case OP_NEG:
                a = VM_Pop(vm);
                VM_Push(vm, VM_NEG(a));
                break;

            case OP_ABS:
                a = VM_Pop(vm);
                VM_Push(vm, (a < 0) ? VM_NEG(a) : a);
                break;

            case OP_SIGN:
                a = VM_Pop(vm);
                VM_Push(vm, (a > 0) ? 1 : (a < 0) ? -1 : 0);
                break;

            /* Bitwise */
            case OP_AND:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, a & b);
                break;

            case OP_OR:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, a | b);
                break;

            case OP_XOR:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, a ^ b);
                break;

            case OP_NOT:
                a = VM_Pop(vm);
                VM_Push(vm, ~a);
                break;

            case OP_SHL:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, wrap32((uint32_t)a << (b & 31)));
                break;

            case OP_SHR:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, a >> (b & 31));
                break;

            /* Comparison */
            case OP_EQ:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a == b) ? 1 : 0);
                break;

            case OP_NE:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a != b) ? 1 : 0);
                break;

            case OP_LT:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a < b) ? 1 : 0);
                break;

            case OP_LE:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a <= b) ? 1 : 0);
                break;

            case OP_GT:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a > b) ? 1 : 0);
                break;

            case OP_GE:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a >= b) ? 1 : 0);
                break;

            /* Logical */
            case OP_LAND:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a && b) ? 1 : 0);
                break;

            case OP_LOR:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a || b) ? 1 : 0);
                break;

            case OP_LNOT:
                a = VM_Pop(vm);
                VM_Push(vm, (!a) ? 1 : 0);
                break;

            /* Min/Max/Clamp */
            case OP_MIN:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a < b) ? a : b);
                break;

            case OP_MAX:
                b = VM_Pop(vm); a = VM_Pop(vm);
                VM_Push(vm, (a > b) ? a : b);
                break;

            case OP_CLAMP:
                c = VM_Pop(vm);  /* hi */
                b = VM_Pop(vm);  /* lo */
                a = VM_Pop(vm);  /* x */
                if (a < b) a = b;
                if (a > c) a = c;
                VM_Push(vm, a);
                break;

            case OP_MAP: {
                int32_t out_hi = VM_Pop(vm);
                int32_t out_lo = VM_Pop(vm);
                int32_t in_hi = VM_Pop(vm);
                int32_t in_lo = VM_Pop(vm);
                int32_t x = VM_Pop(vm);

                if (in_hi == in_lo) {
                    VM_Push(vm, out_lo);
                } else {
                    /* differences in 64 bits: map(x, -2e9, 2e9, ...) overflowed
                     * int32 before the widening multiply */
                    int64_t result = ratio_mul((int64_t)x - in_lo, (int64_t)out_hi - out_lo,
                                               (int64_t)in_hi - in_lo);
                    result += out_lo;
                    VM_Push(vm, wrap32((uint32_t)result));
                }
                break;
            }

            case OP_LERP: {
                int32_t t = VM_Pop(vm);   /* 0-1000 permille */
                b = VM_Pop(vm);
                a = VM_Pop(vm);
                int64_t result = (int64_t)a * (1000 - (int64_t)t) + (int64_t)b * t;
                VM_Push(vm, wrap32((uint32_t)(result / 1000)));
                break;
            }

            /* Conditionals */
            case OP_IF:
                c = VM_Pop(vm);  /* else value */
                b = VM_Pop(vm);  /* then value */
                a = VM_Pop(vm);  /* condition */
                VM_Push(vm, a ? b : c);
                break;

            case OP_SWITCH:
                if (pc < length) {
                    uint8_t count = code[pc++];
                    a = VM_Pop(vm);  /* index */

                    if (a >= 0 && a < count && pc + (a + 1) * 4 <= length) {
                        uint16_t offset = pc + a * 4;
                        int32_t val = wrap32((uint32_t)code[offset] | ((uint32_t)code[offset+1] << 8) |
                                               ((uint32_t)code[offset+2] << 16) | ((uint32_t)code[offset+3] << 24));
                        VM_Push(vm, val);
                    } else if (count > 0 && pc + count * 4 <= length) {
                        uint16_t offset = pc + (count - 1) * 4;
                        int32_t val = wrap32((uint32_t)code[offset] | ((uint32_t)code[offset+1] << 8) |
                                               ((uint32_t)code[offset+2] << 16) | ((uint32_t)code[offset+3] << 24));
                        VM_Push(vm, val);
                    } else {
                        VM_Push(vm, 0);
                    }
                    pc += count * 4;
                }
                break;

            case OP_LOAD_IDX:
                if (pc + 2 <= length) {
                    id = code[pc] | (code[pc + 1] << 8);
                    pc += 2;
                    a = VM_Pop(vm);                     /* index */
                    if (a >= 0 && a <= 0xFFFF - id) {
                        VM_Push(vm, Channel_GetValue(vm->channels,
                                                     (uint16_t)(id + a)));
                    } else {
                        VM_Push(vm, 0);
                    }
                }
                break;

            case OP_STORE_IDX:
                if (pc + 2 <= length) {
                    id = code[pc] | (code[pc + 1] << 8);
                    pc += 2;
                    a = VM_Pop(vm);                     /* index */
                    b = VM_Pop(vm);                     /* value */
                    /* same bound as LOAD_IDX: an index past 0xFFFF wrapped
                     * the channel id and wrote an unrelated channel */
                    if (a >= 0 && a <= 0xFFFF - id) {
                        Channel_SetValue(vm->channels,
                                         (uint16_t)(id + a), b);
                    }
                }
                break;

            case OP_STORE_CH:
                if (pc + 2 <= length) {
                    id = code[pc] | (code[pc + 1] << 8);
                    a = VM_Pop(vm);
                    Channel_SetValue(vm->channels, id, a);
                    pc += 2;
                }
                break;

            case OP_TABLE_2D:
            case OP_TABLE_3D:
            case OP_TIMER_VAL:
            case OP_FILTER_VAL:
                if (pc + 2 <= length) {
                    pc += 2;
                    VM_Push(vm, 0);
                }
                break;

            case OP_END:
                return VM_Peek(vm);

            default:
                break;
        }
    }

    return VM_Peek(vm);
}
