/**
 * @file vm.h
 * @brief Bytecode Virtual Machine for expression evaluation
 *
 * RPN (Reverse Polish Notation) stack-based VM.
 * Evaluates channel expressions in topologically sorted order.
 */

#ifndef VM_H
#define VM_H

#include <stdint.h>
#include <stdbool.h>
#include "../channel_engine/channel.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Opcode Definitions
 * ============================================================================ */

typedef enum {
    /* Stack operations */
    OP_NOP          = 0x00,
    OP_PUSH_CONST   = 0x01,     /* Push 32-bit constant */
    OP_PUSH_CH      = 0x02,     /* Push channel value by ID */
    OP_POP          = 0x03,     /* Discard top */
    OP_DUP          = 0x04,     /* Duplicate top */
    OP_SWAP         = 0x05,     /* Swap top two */
    OP_ROT          = 0x06,     /* Rotate top three: a b c → b c a */

    /* Arithmetic */
    OP_ADD          = 0x10,     /* a + b */
    OP_SUB          = 0x11,     /* a - b */
    OP_MUL          = 0x12,     /* a * b */
    OP_DIV          = 0x13,     /* a / b (integer) */
    OP_MOD          = 0x14,     /* a % b */
    OP_NEG          = 0x15,     /* -a */
    OP_ABS          = 0x16,     /* abs(a) */
    OP_SIGN         = 0x17,     /* sign(a): -1, 0, +1 */

    /* Bitwise */
    OP_AND          = 0x20,     /* a & b */
    OP_OR           = 0x21,     /* a | b */
    OP_XOR          = 0x22,     /* a ^ b */
    OP_NOT          = 0x23,     /* ~a */
    OP_SHL          = 0x24,     /* a << b */
    OP_SHR          = 0x25,     /* a >> b */

    /* Comparison (push 0 or 1) */
    OP_EQ           = 0x30,     /* a == b */
    OP_NE           = 0x31,     /* a != b */
    OP_LT           = 0x32,     /* a < b */
    OP_LE           = 0x33,     /* a <= b */
    OP_GT           = 0x34,     /* a > b */
    OP_GE           = 0x35,     /* a >= b */

    /* Logical */
    OP_LAND         = 0x40,     /* a && b (logical AND) */
    OP_LOR          = 0x41,     /* a || b (logical OR) */
    OP_LNOT         = 0x42,     /* !a (logical NOT) */

    /* Min/Max/Clamp */
    OP_MIN          = 0x50,     /* min(a, b) */
    OP_MAX          = 0x51,     /* max(a, b) */
    OP_CLAMP        = 0x52,     /* clamp(x, lo, hi) */
    OP_MAP          = 0x53,     /* map(x, in_lo, in_hi, out_lo, out_hi) */
    OP_LERP         = 0x54,     /* lerp(a, b, t) where t=0..1000 (permille) */

    /* Conditionals */
    OP_IF           = 0x60,     /* cond ? a : b (pop cond, a, b, push result) */
    OP_SWITCH       = 0x61,     /* switch(idx) followed by case count + values */

    /* Table lookup (operand: table_id) */
    OP_TABLE_2D     = 0x70,     /* table2d[x] */
    OP_TABLE_3D     = 0x71,     /* table3d[x, y] */

    /* Indexed channel access (operand: base channel id).
     * LOAD: pop idx, push channel[base+idx].
     * STORE: pop idx, pop value, channel[base+idx] = value. */
    OP_LOAD_IDX     = 0x72,
    OP_STORE_IDX    = 0x73,

    /* Timer/Filter refs (operand: node_id) */
    OP_TIMER_VAL    = 0x80,     /* timer output value */
    OP_FILTER_VAL   = 0x81,     /* filter output value */

    /* Output */
    OP_STORE_CH     = 0x90,     /* Pop and store to channel */
    OP_END          = 0xFF,     /* End of program */
} VMOpcode_t;

/* ============================================================================
 * VM Types
 * ============================================================================ */

#ifndef VM_MAX_BYTECODE
#define VM_MAX_BYTECODE 4096 /* STM32F446 profile; Racing Dash default is larger */
#endif
#ifndef VM_STACK_SIZE
#define VM_STACK_SIZE 48 /* STM32F446 profile; Racing Dash default is larger */
#endif
#ifndef VM_MAX_PROGRAMS
#define VM_MAX_PROGRAMS 16 /* STM32F446 profile; Racing Dash default is larger */
#endif

typedef struct {
    uint16_t channel_id;        /* Output channel */
    uint16_t offset;            /* Offset in bytecode buffer */
    uint16_t length;            /* Bytecode length */
    uint8_t  priority;          /* Execution priority (topo order) */
    uint8_t  flags;
} VMProgram_t;

#define VM_PROG_ENABLED     (1 << 0)
#define VM_PROG_DIRTY       (1 << 1)

typedef struct {
    /* Bytecode storage */
    uint8_t  bytecode[VM_MAX_BYTECODE];
    uint16_t bytecode_used;

    /* Programs */
    VMProgram_t programs[VM_MAX_PROGRAMS];
    uint16_t program_count;

    /* Execution order (indices into programs[], topologically sorted) */
    uint8_t  exec_order[VM_MAX_PROGRAMS];
    uint8_t  exec_count;

    /* Runtime stack */
    int32_t  stack[VM_STACK_SIZE];
    uint8_t  sp;

    /* Reference to channel engine */
    ChannelEngine_t* channels;

    /* Stats */
    uint32_t total_ops;
    uint32_t total_programs;

    bool initialized;
} VM_t;

/* ============================================================================
 * Public API
 * ============================================================================ */

/**
 * @brief Initialize VM
 */
void VM_Init(VM_t* vm, ChannelEngine_t* channels);

/**
 * @brief Reset VM state
 */
void VM_Reset(VM_t* vm);

/**
 * @brief Add bytecode program for a channel
 * @param channel_id Output channel ID
 * @param bytecode Bytecode array
 * @param length Bytecode length
 * @return Program index or -1 on error
 */
int VM_AddProgram(VM_t* vm, uint16_t channel_id,
                  const uint8_t* bytecode, uint16_t length);

/**
 * @brief Remove program for channel
 */
void VM_RemoveProgram(VM_t* vm, uint16_t channel_id);

/**
 * @brief Build execution order based on dependencies
 */
void VM_BuildExecutionOrder(VM_t* vm);

/**
 * @brief Execute all programs in order
 */
void VM_ExecuteAll(VM_t* vm);

/**
 * @brief Execute single program
 */
int32_t VM_ExecuteProgram(VM_t* vm, uint8_t prog_idx);

/**
 * @brief Execute bytecode fragment
 * @return Top of stack after execution
 */
int32_t VM_Execute(VM_t* vm, const uint8_t* code, uint16_t length);

/* ============================================================================
 * Stack Operations
 * ============================================================================ */

static inline void VM_Push(VM_t* vm, int32_t value)
{
    if (vm->sp < VM_STACK_SIZE) {
        vm->stack[vm->sp++] = value;
    }
}

static inline int32_t VM_Pop(VM_t* vm)
{
    return (vm->sp > 0) ? vm->stack[--vm->sp] : 0;
}

static inline int32_t VM_Peek(VM_t* vm)
{
    return (vm->sp > 0) ? vm->stack[vm->sp - 1] : 0;
}

#ifdef __cplusplus
}
#endif

#endif /* VM_H */
