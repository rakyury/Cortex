# Actual firmware C simulations

These tests connect the real `HalCanClient` and the Qt upload UI to a subprocess
compiled from the paired SynapsePDM `pdm_link.c`, channel engine and Racing Dash
VM. Frames cross a simulated CAN adapter, with loss, duplication and corruption
injected at that boundary. This is a software protocol/VM simulation, not an
electrical CAN, GPIO, ADC or STM32 instruction-set emulator.

Set `PDM_FIRMWARE_ROOT` to the paired SynapsePDM checkout containing
`pdm_cube/tests/link_simulator.c`, and optionally set `CC` to a host C compiler.
Install `r2_pdm_configurator/tests/requirements-ci.txt`, then run from Cortex:

```sh
python -m unittest discover -s r2_pdm_configurator/integration -p 'test_*.py' -v
```

Set `QT_QPA_PLATFORM=offscreen` for headless Qt. Linux CI sets
`PDM_LINK_SANITIZE=1` to compile the actual C with ASan/UBSan. The dedicated
workflow pins an immutable SynapsePDM commit and preserves test/compiler logs.
The paired implementation is `rakyury/SynapsePDM@c0058f829bd82df7941590db964c66f30c131ce8`.
Missing firmware or compiler is a failure, never an implicit skipped test.

The scenarios cover passive connection, telemetry, successful upload and actual
VM result, Qt table/graph integration, corrupted CRC retaining the previous
program, missing data and staging timeout, repeated/out-of-order data, lost
COMMIT ACK with explicit CRC reconciliation, firmware reset, heartbeat loss,
and rejected store/unknown-source/opcode programs. Every scenario that uploads
checks observable output channels remain zero. Firmware host tests additionally
check bounded queues, frame validation, output policy invariants, HAL mocks
and bytecode validation/fuzz cases.

Checks include cleanup regressions that ensure a failed C simulator/sanitizer
exit cannot be swallowed by the Qt disconnect handler. ADC fixtures exercise the
real C conversion model and wire encoding before checking Python/Qt values.

The start-admission tests compile the paired `pdm_start_admission.c` and board
profiles into a local host process. They exercise both 14/24-channel profiles
with 2, 3 and 4 start slots, FIFO, current reservations, total/group limits,
faults and reset. Fixtures are fictional simulation values, not approved board
limits. No CAN adapter is created for this simulator. Its virtual STARTING/ON
states never represent physical gate activation; physical gate indicators stay OFF.

The gate-state assertions are software invariants. Physical pin levels,
boot/reset pull-downs, transceiver behavior, load currents and timing still
require separate HIL. Programs are RAM-only diagnostic expressions, not a
general output-controller graph or firmware flashing protocol.
