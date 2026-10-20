"""Read-only subset of R2 C VM bytecode; never evaluates Python expressions."""
from ..channel_engine import Snapshot

PROFILE = "r2-pdm-preview-v1"
MAX_BYTES = 512
MAX_OPS = 256
MAX_STACK = 48  # same STM32F446 profile as pdm_cube/src/r2_shared/vm/vm.h


class PreviewError(ValueError):
    pass


def wrap(value):
    return ((value + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)


def cdiv(a, b):
    """C integer division truncates toward zero (Python // does not)."""
    return (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)


def ratio_mul(dx, dy, span):
    # Exact algorithm from the pinned C implementation, including low-bit loss.
    while abs(dy) >= 0x80000000 and (abs(dx) >= 0x80000000 or abs(span) >= 0x80000000):
        dx, span = cdiv(dx, 2), cdiv(span, 2)
    return cdiv(dx * dy, span) if span else 0


_UNARY = {0x15, 0x16, 0x17, 0x23, 0x42}
_BINARY = set(range(0x10, 0x15)) | {0x20, 0x21, 0x22, 0x24, 0x25} | set(range(0x30, 0x36)) | {0x40, 0x41, 0x50, 0x51}


def validate(code):
    """Reject malformed, writing, unsupported and over-budget bytecode first."""
    if not isinstance(code, bytes) or not 1 <= len(code) <= MAX_BYTES:
        raise PreviewError("bytecode must be immutable bytes, 1..512 bytes")
    pc, depth, operations = 0, 0, []
    while pc < len(code):
        op = code[pc]
        pc += 1
        arg, need, delta, size = None, 0, 0, 0
        if op == 0x01:
            size, delta = 4, 1
        elif op == 0x02:
            size, delta = 2, 1
        elif op == 0x72:
            size, need = 2, 1
        elif op == 0x61:
            if pc == len(code):
                raise PreviewError("missing switch count")
            count = code[pc]
            pc += 1
            size, need = 4 * count, 1
        elif op == 0x03:
            need, delta = 1, -1
        elif op == 0x04:
            need, delta = 1, 1
        elif op == 0x05:
            need = 2
        elif op == 0x06:
            need = 3
        elif op in _UNARY:
            need = 1
        elif op in _BINARY:
            need, delta = 2, -1
        elif op in {0x52, 0x54, 0x60}:
            need, delta = 3, -2
        elif op == 0x53:
            need, delta = 5, -4
        elif op not in {0x00, 0xFF}:
            raise PreviewError(f"opcode 0x{op:02x} is not allowed in read-only profile")
        if pc + size > len(code):
            raise PreviewError("truncated operand")
        if size or op == 0x61:
            if op == 0x61:
                arg = tuple(int.from_bytes(code[i:i + 4], "little", signed=True)
                            for i in range(pc, pc + size, 4))
            else:
                arg = int.from_bytes(code[pc:pc + size], "little", signed=op == 0x01)
            pc += size
        if depth < need:
            raise PreviewError("stack underflow")
        depth += delta
        if depth > MAX_STACK:
            raise PreviewError("stack limit exceeded")
        operations.append((op, arg))
        if len(operations) > MAX_OPS:
            raise PreviewError("instruction budget exceeded")
        if op == 0xFF:
            if pc != len(code) or depth != 1:
                raise PreviewError("END must be final with exactly one result")
            return tuple(operations)
    raise PreviewError("missing END")


def execute(code, snapshot=None):
    operations = validate(code)
    channels = snapshot if snapshot is not None else Snapshot()
    if not isinstance(channels, Snapshot):
        raise PreviewError("a validated Snapshot is required")
    stack = []
    for op, arg in operations:
        if op == 0x00:
            continue
        if op == 0xFF:
            return stack[-1]
        if op == 0x01:
            stack.append(arg)
        elif op == 0x02:
            stack.append(channels.get(arg))
        elif op == 0x03:
            stack.pop()
        elif op == 0x04:
            stack.append(stack[-1])
        elif op == 0x05:
            stack[-2:] = reversed(stack[-2:])
        elif op == 0x06:
            stack[-3:] = stack[-2:] + [stack[-3]]
        elif op == 0x72:
            index = stack.pop()
            stack.append(channels.get(arg + index) if 0 <= index <= 65535 - arg else 0)
        elif op == 0x61:
            index = stack.pop()
            stack.append(arg[index] if 0 <= index < len(arg) else arg[-1] if arg else 0)
        elif op in _UNARY:
            a = stack.pop()
            if op == 0x15: result = -a
            elif op == 0x16: result = abs(a)
            elif op == 0x17: result = (a > 0) - (a < 0)
            elif op == 0x23: result = ~a
            else: result = int(not a)
            stack.append(wrap(result))
        elif op in _BINARY:
            b, a = stack.pop(), stack.pop()
            if op == 0x10: result = a + b
            elif op == 0x11: result = a - b
            elif op == 0x12: result = a * b
            elif op == 0x13: result = cdiv(a, b) if b else 0
            elif op == 0x14: result = a - cdiv(a, b) * b if b else 0
            elif op == 0x20: result = a & b
            elif op == 0x21: result = a | b
            elif op == 0x22: result = a ^ b
            elif op == 0x24: result = a << (b & 31)
            elif op == 0x25: result = a >> (b & 31)
            elif op == 0x30: result = int(a == b)
            elif op == 0x31: result = int(a != b)
            elif op == 0x32: result = int(a < b)
            elif op == 0x33: result = int(a <= b)
            elif op == 0x34: result = int(a > b)
            elif op == 0x35: result = int(a >= b)
            elif op == 0x40: result = int(bool(a) and bool(b))
            elif op == 0x41: result = int(bool(a) or bool(b))
            elif op == 0x50: result = min(a, b)
            else: result = max(a, b)
            stack.append(wrap(result))
        elif op == 0x53:
            out_hi, out_lo, in_hi, in_lo, x = (stack.pop() for _ in range(5))
            stack.append(wrap(out_lo + ratio_mul(x - in_lo, out_hi - out_lo, in_hi - in_lo))
                         if in_hi != in_lo else out_lo)
        else:
            c, b, a = stack.pop(), stack.pop(), stack.pop()
            if op == 0x52: result = min(max(a, b), c)
            elif op == 0x60: result = b if a else c
            else:
                if not 0 <= c <= 1000:
                    raise PreviewError("LERP permille must be 0..1000")
                result = cdiv(a * (1000 - c) + b * c, 1000)
            stack.append(wrap(result))
    raise PreviewError("missing result")
