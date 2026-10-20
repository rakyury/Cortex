# HAL diagnostics and RAM VM upload

Choose **STM32Cube HAL diagnostics** in the Protocol selector before connecting.
The selector is locked for a connection. HAL mode uses the dedicated 0x580/0x590
protocol and never polls or writes the legacy configuration IDs. Connect and
normal telemetry refresh send no frames. **Read active CRC** is an explicit,
read-only query.

The table, channel tree and graph show the actual HAL channels: output gate
states 200–213, interlock 230, CAN counters/ID/errors and uptime/faults 240–244,
and diagnostic VM result 300. Output gates must stay zero; interlock must stay
one. The UI suppresses invariant violations and stale/unavailable values from
the numeric graph. Freshness uses the transport's 500 ms heartbeat and 1 second
sample deadlines. Channel 300 is unavailable until the device reports an active
program. ADC current, battery voltage, temperature and physical inputs are not
implemented and are never synthesized as live measurements.

The **HAL diagnostic VM** dock is separate from the local computed preview.
Choose an implemented diagnostic source and simple operation/constant, or load
a validated binary of at most 128 bytes. Reading result channel 300, stores,
unknown instructions and unimplemented sensor sources are rejected. Loading or
replacing a binary disarms upload; a rejected replacement clears the previous
binary to prevent accidental reuse. The existing local computed definitions
remain available when returning to Legacy mode.

Upload requires a fresh compatible heartbeat, active interlock, upload
capability, explicit arm and a confirmation dialog. Freshness is checked again
after confirmation. The program is RAM-only and has no output-write opcode;
integer ADD/SUB follow the device's signed int32 wrapping semantics. Every new
upload requires new arming. Status displays pending, failure, unknown, CRC-only
readback, or correlated ACK plus matching CRC. A CRC-only readback without an
expected program does not mean verified. Lost COMMIT acknowledgement is unknown;
query separately rather than automatically retrying COMMIT.

This path is for simulation first. CAN diagnostic transmit/ACK behavior is
separate from physical output actuation. Output pins remain OFF until reviewed
HIL validation. No flash or EEPROM write is performed by this UI.

Run the offscreen GUI tests:

```sh
python -m unittest discover -s r2_pdm_configurator/ui/tests -v
```

The tests use fake adapters, actual HalCanClient framing and a controlled clock.
They cover zero connect/poll/legacy traffic, confirmation cancellation and
expiry, binary rejection, transport errors, fresh/stale display, graph gaps,
status truthfulness and preservation of local definitions across protocol
switches. Device-side semantics are covered separately by the C/Python simulator.
