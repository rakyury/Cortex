# R2 PDM preview profile v1

This is an offline, read-only preview of the actual Racing Dash C integer VM
used by the experimental STM32Cube PDM port. It has no CAN, serial, configuration
write, flashing or output-control capability. Its result is telemetry only.
The GUI connects this preview through the local computed-channel editor
(see `../ui/COMPUTED_CHANNELS.md`); it does not enable any PDM output.

## Compatibility and limits

`execute(bytecode, Snapshot({...}))` returns one signed int32. Opcodes and
little-endian operands match `shared/vm/vm.h`: stack operations, arithmetic,
bitwise shifts, comparisons, logical operations, min/max/clamp/map/lerp,
conditional/switch, channel reads and bounded indexed reads. Arithmetic wraps
as int32, division truncates toward zero, division by zero returns zero, and
INT32_MIN / -1 wraps to INT32_MIN exactly as in C. Map uses the C ratio reduction
algorithm, including its documented loss of low bits at large ranges.

The profile caps bytecode at 512 bytes, 256 instructions, 48 stack slots and
128 snapshot channels including eight system defaults. Programs must end with
END and exactly one value. The validator rejects truncated operands, unknown
instructions, stack errors and trailing bytes before evaluation. STORE_CH and
STORE_IDX are always rejected, including writes aimed at gate/interlock channels.
No Python eval/exec or expression compilation is used.

Snapshots accept only uint16 IDs and int32 values; NaN, infinity, booleans,
floats and out-of-range values are rejected. Missing channels return zero as in
the C engine; callers must separately gate stale/invalid telemetry. Peak channel
history is not represented and those reads are rejected. LERP is restricted to
its documented 0..1000 permille domain. Invalid bytecode is rejected more strictly
than the permissive C VM; parity claims apply only to accepted inputs.

`channel_engine.nodes` supplies bounded SMA (1..16), EMA (0..255 alpha), TON
on-delay, linear 2D table (1..16 strictly ordered points) and hysteresis alarm
previews matching the C managers. Instantiate them once and update per tick in
the same order as the device managers. Timer calls require a monotonic uint32
millisecond clock modulo rollover and gaps below 2^31 milliseconds. These are
offline components, not a multi-program scheduler or a full R2 design loader.
The remaining filter/timer/table types are outside v1. TABLE_2D/TABLE_3D,
TIMER_VAL and FILTER_VAL opcodes are rejected because the pinned C VM currently
implements those opcode handlers as placeholder zeros; node manager outputs can
instead be placed in a snapshot and consumed through PUSH_CH.

## Actual C parity evidence

`reference/` contains unmodified C/header copies from
`rakyury/SynapsePDM@ba114a8c2599bd31800ba3e06dfb08b248cc4ea5`, under
`pdm_cube/src/r2_shared`. The original Racing Dash implementation was inspected at
`rakyury/racing_dash@d3af311d61fb0e8848bea474fff7228d7e8eecc7`. The PDM profile uses
smaller static manager limits; the manifest records each source's LF-normalized
SHA-256 so a change cannot silently alter the parity oracle. These sources are
test fixtures, not a separately maintained production firmware fork. Update
them only with a reviewed firmware reference change and rerun all vectors.

Run `python -m unittest r2_pdm_configurator.tests.test_vm_preview -v`.
The suite compiles the pinned C VM, channel, filter, timer and table implementations
into an isolated executable and compares thousands of integer/overflow/boundary
vectors and a stateful sequence including timer rollover. It also performs 2,000
seeded malformed-bytecode fuzz cases and verifies that write instructions fail.
A C compiler is required (CC environment variable, then cc/clang/gcc on PATH).
Set PDM_VM_SANITIZE=1 on supported compilers to run ASan/UBSan; dedicated Linux
CI enables both. Windows uses the ISO C CRT compatibility macro for unchanged
embedded sources rather than substituting Microsoft-only string APIs.

Hardware parity, firmware loading, full graph scheduling and any use of computed
channels to actuate loads remain outside this preview and require review/HIL.
