# R2 Synapse PDM — CAN configurator (prototype)

A **separate** PyQt6 configurator alongside the existing Cortex .NET/USB tool.
Uses the R2 Racing Dash dark diagnostics workflow with adapter selection, channel
tree, live values, local R2 VM computed channels, and protected CAN configuration.

## Run

From the repository root, with Python 3.10+:

    python -m pip install PyQt6 python-can
    python r2_pdm_configurator/app.py

Select ECUMaster USBtoCAN, PEAK PCAN-USB, SLCAN/CANable, SocketCAN,
Kvaser, Vector, or a virtual test bus. Set baud rate to match the PDM
(default: 500 kbit/s). Connecting does not actuate any load.

For ECUMaster: reuse the **R2 Racing Dash adapter bridge** by setting
R2_DASH_CONFIGURATOR_SRC to the absolute path of
racing_dash/configurator_qt/src. That bridge needs the 32-bit helper Python,
EUSB2CAN.dll, and original project installation. No proprietary binaries
are copied into this project. See core/can_eusb2can.py in R2 Racing Dash.

## Computed channels

The **Computed channels · local preview** dock creates named values using the
R2 VM, with results in the telemetry table, channel tree and graph. For example,
Voltage / Less than / Constant 11500 creates a low-battery indicator below
11.5 V. Definitions can be saved and loaded locally. Missing or stale inputs
produce no numeric result. See [Computed channels](ui/COMPUTED_CHANNELS.md)
for units, supported operations and limits.

This is a local diagnostic preview: programs are not uploaded and results cannot
be bound to hardware outputs. The experimental STM32Cube port keeps CAN silent
and physical outputs OFF pending HIL; it does not yet provide live telemetry
to this GUI.

## Protocol

Synapse PDM is **Classic CAN, 11-bit IDs**, not the dash 29-bit bulk
transport. Message layout follows the original SynapsePDM.dbc and
CANComms.cpp:
- System: 0x720 and 0x721 every 100 ms (voltage, current, temp, faults).
- Channel status: 0x700 request / 0x701 four-frame response.
- Digital inputs: 0x710 request / 0x711 response.
- Analogue inputs: 0x712 request / 0x713 two-frame response.
- Channel configuration: 0x740..0x74E (only supported options are exposed).
- System configuration: 0x730..0x736.

CAN configuration writes require explicit ARM, a fresh heartbeat, and a
confirmation dialog. **There is no write acknowledgement or atomic rollback in
the stock protocol.** Verify settings after writing and NEVER modify
safety-critical loads while powered. Live output actuation is intentionally
not exposed in the GUI.

IMPORTANT limitations: channel status responses do not include an echoed
channel number or transaction ID. Polling is serialized, but delayed frames
from earlier requests cannot be unambiguously identified. This prototype
requires bench testing and an extended PDM protocol before critical use.
Existing USB Cortex features such as full firmware flashing are not yet
implemented over CAN.

## Tests

    python -m pip install -r r2_pdm_configurator/tests/requirements-ci.txt
    python -m unittest discover -s r2_pdm_configurator/tests -v
    python -m unittest discover -s r2_pdm_configurator/ui/tests -v

Tests use fake/virtual CAN buses and offscreen Qt; no physical hardware is
required. Install a host C compiler for the compiled Racing Dash VM parity and
fuzz tests. CI runs both test suites and the compiled C checks.
