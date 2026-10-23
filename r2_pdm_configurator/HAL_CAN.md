# Experimental HAL diagnostic link

`hal_can.HalCanClient` uses the version 1 contract below, separate from the
legacy `PdmCanClient` and its 0x700-series configuration/output commands.
Opening this client, receiving telemetry, and ordinary polling send no frames.
The device runs CAN in **normal mode and acknowledges bus frames**. This is
different from the earlier silent monitor. Its physical outputs remain
interlocked OFF; this link has no command for changing that interlock.

All frames are standard Classic CAN DATA frames with exactly eight bytes.
All multi-byte integers are little-endian. One PDM per bus is supported.

| ID | Payload |
| --- | --- |
| 0x580 heartbeat | version u8, flags u8, sequence u16, uptime ms u32 |
| 0x581 sample | channel u16, quality u8, sequence u8, value i32 |
| 0x582 ACK | transaction u32, operation u8, status u8, next offset u16 |
| 0x583 readback | transaction u32, active program CRC32 u32 |
| 0x584 ADC sample | channel u16, quality u8, sequence u8, value i32 |
| 0x590 BEGIN | transaction u32, length u16, version=1 u8, reserved=0 u8 |
| 0x591 DATA | transaction u32, byte offset u16, two code bytes |
| 0x592 COMMIT | transaction u32, CRC32 u32 |
| 0x593 ABORT | transaction u32, zero u32 |
| 0x594 QUERY | transaction u32, zero u32 |

Heartbeat flags: bit 0 interlocked (required), bit 1 active VM, bit 2 upload
supported. Unknown bits/version or an absent interlock reject the session.
Heartbeat freshness is 500 ms, sample freshness 1000 ms. Restart, expiry,
disconnect and transport failure clear samples, upload state and authorization.
Heartbeat sequence/uptime and sample sequence rollover are supported. Duplicate
or backwards samples are ignored within the freshness window; a stale channel
may establish a new sequence baseline after a long gap.

Samples cover gates 200–213 (zero), interlock 230 (one), RX count 240, last RX ID
241, CAN errors 242, uptime 243, fault mask 244, and diagnostic VM result 300.
Quality 0 means valid; quality 1 means unavailable. Channel 300 is usable only
while the heartbeat reports an active VM. No voltage, current, temperature or
unimplemented sensor readings are fabricated. Nonzero gate/zero interlock
diagnostics invalidate the session and stop upload progression.

## Upload and verification

The additive ADC frame has a separate channel/quality namespace. Channel 100
is battery millivolts, 101 the sum of 14 sense-current estimates in milliamps,
102 MCU die temperature in milli-degrees C, and 110–123 individual sense
currents in milliamps. Qualities 0 (factory calibrated) and 2 (nominal board
calibration) are numeric. Qualities 1 (unavailable), 3 (invalid/fault) and 4
(stale acquisition) must appear as gaps. Nominal current is an IS-pin estimate,
including while gates are OFF; it is not an independently measured load current.

ADC and diagnostic frames alternate, one sample total per 10 ms tick. Healthy
diagnostic rounds take 420 ms and ADC rounds 340 ms. The firmware refreshes
queued ADC payloads from its model, which marks capture age at 500 ms. The
client separately expires ADC observations after 1000 ms; receiving heartbeats
or diagnostics cannot refresh them. ADC samples share `samples`/`sample_valid`
with diagnostics, while `adc_rx_frames` and `adc_bad_frames` distinguish their
receive/rejection counts. The original 0x581 still permits only qualities 0/1.

The client enforces battery 0–20000 mV, die temperature −40000–125000 m°C,
individual current 0–36816 mA and total current 0–515424 mA. Numeric payloads
outside these bounds replace the prior reading with an invalid gap. Unknown
channels/qualities and malformed frames are rejected. ADC channels are never
accepted as VM sources, so sensor faults cannot silently become VM zero inputs.

`begin_upload(code, confirmed=True)` requires a fresh compatible heartbeat,
upload capability and `client.armed = True`. Authorization is consumed when
the transaction starts; each subsequent upload must be armed and confirmed
again. Programs are immutable bytes, at most 128 bytes. Upload is RAM-only;
reset loses the active program. The result destination is always channel 300.

Only PUSH_CONST (0x01), PUSH_CH (0x02), ADD/SUB (0x10/0x11), comparisons
(0x30–0x35), logical operations (0x40–0x42), MIN/MAX (0x50/0x51) and END (0xff)
are accepted. Source IDs are 200–213, 230 and 240–244. END must be final with
exactly one result, and stack depth may not exceed 48. Stores, feedback from
300, unsupported sensors, tables, timers, indexed operations and unknown
opcodes are rejected before sending. ADD/SUB wrap as signed int32, matching
the vendored Racing Dash C VM; they do not saturate.

Every operation uses a random nonzero transaction ID and stop-and-wait
correlation. Successful ACK offsets are BEGIN=0, DATA=next byte, COMMIT=length,
ABORT=0 and QUERY=0. Odd final data bytes are padded with zero, excluded from
the IEEE/zlib CRC32. An ACK with the wrong ID, operation or offset never
advances the transaction. Status codes 1–7 report malformed, busy/wrong
transaction, offset, CRC, bytecode, expired staging, or no active program.

The host waits 350 ms for a reply and never retries an operation automatically.
In particular, a lost COMMIT response yields **unknown**, not success or safe
failure. `query_readback()` explicitly requests active-code CRC using a new
transaction ID; after an unknown upload it compares against that upload's CRC.
A matching CRC and correctly correlated successful ACK are both required for
`upload.verified`. Their arrival order does not matter. A standalone query
with no expected CRC reports `readback`, which is not verified.

`cancel()` disarms and sends a matching ABORT only while BEGIN/DATA is pending.
After COMMIT has been sent, cancellation reports unknown and cannot promise
to remove the active program. Abandoning a staging transfer leaves firmware
to discard it after its 1000 ms inactivity timeout. A previous active program
survives incomplete/invalid uploads. A capability-loss heartbeat immediately
stops pending upload progression.

## Public API and tests

Construct `HalCanClient(bus, now=time.monotonic)` with a python-can-compatible
bus. `tick(receive_budget=64)` is nonblocking on receive and bounded to at most
256 frames per call; sends request a 50 ms adapter timeout. `close()` is
idempotent. `online()`, `can_upload()` and `sample_valid(channel)` expose
freshness/capability decisions. `can_upload()` does not include the user arm.

`samples[channel]` is a frozen `HalSample(channel, value, quality, sequence,
last_seen)`. `upload` is replaced with a frozen `HalUploadStatus` on each state
change: state, tid, offset, length, expected_crc, active_crc, error and the
derived boolean verified. `heartbeat_last_seen`, `interlocked`, `compatible`,
`upload_supported`, `vm_active`, `rx_frames`, `bad_frames` and `transport_errors`
support presentation and diagnostics. `validate_program(code)` returns valid
bytes or raises ValueError; no CAN or device operation occurs during validation.

Run `python -m unittest r2_pdm_configurator.tests.test_hal_can -v`. These unit
tests inject malformed and stale messages, loss, reboot, cancellation, CRC
mismatch, reply reordering and transport failure. Separate integration tests
drive the actual C firmware simulator. Neither test suite establishes real
adapter, bus, electrical or physical output safety; hardware/HIL approval is
still required.
