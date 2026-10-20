# AGENTS.md — Cortex and R2 Synapse PDM CAN configurator

This fork hosts TWO distinct applications:
- Original Avalonia/.NET Cortex configurator, including USB/serial config.
- New prototype Python/PyQt6 configurator in `r2_pdm_configurator/`, inspired by R2 Racing Dash.

## Branch / integration
- Cortex integration branch: `feature/r2-pdm-can-configurator`; open integration draft PR #1.
- Work on independent issue branches `agent/pdm-can-*`, `agent/pdm-ui-*`, or `agent/pdm-vm-*` based on the latest integration branch.
- Submit PRs to the integration branch; never modify `main` directly. No force-push, silent auto-merge, or unsafe controller writes.
- Keep legacy Cortex fully functional. Do not silently replace its USB transport with CAN.
- R2 Racing Dash is the architectural reference: https://github.com/rakyury/racing_dash
  (`configurator_qt/src/ui/main_window.py`, `src/models/channels.py`, `shared/vm/`, `src/core/live_stream.py`).

## Agent roles and file ownership

### 1. CAN transport / telemetry
Own `r2_pdm_configurator/pdm_can.py`, its protocol tests and adapters under `r2_pdm_configurator/transports/`.
- Classic CAN only, default 11-bit IDs 0x700 / 0x710 / 0x712 / 0x720 / 0x730 / 0x740.
- Do not copy the dash's 29-bit bulk transport to PDM.
- Use native python-can adapters (PCAN, SLCAN, SocketCAN, Kvaser, Vector). Reuse the existing R2 Racing Dash ECUMaster EUSB2CAN bridge; never commit vendor DLLs.
- Request/reply frames have NO transaction IDs; serialize channel status requests and flag ambiguous results.
- Do not send write frames just to discover devices or populate UI.
- Explicit arm + confirmation + recent heartbeat for writes; every config write requires readback and discrepancy display.
- Coordinate protocol improvements with firmware CAN agent; add versioned protocol tests.

### 2. GUI / user experience
Own `r2_pdm_configurator/app.py`, `r2_pdm_configurator/ui/`, `r2_pdm_configurator/models/` excluding CAN transport, and UI tests.
- Match R2 Racing Dash approach: dockable channel tree, editable properties, live telemetry, graph, connection dialog, dark theme and diagnostics.
- Keep state/model separate from QWidget tree to support headless tests.
- Display stale, degraded or invalid telemetry prominently. Never display stale current as live.
- Maintain all 14 outputs, 8 digital and 8 analogue inputs; keep accessible control labels and warnings.
- Do NOT expose uncontrolled output actuation in main UI or default-on behavior.

### 3. Channel VM and computed telemetry
Own new `r2_pdm_configurator/vm/`, `r2_pdm_configurator/channel_engine/` and associated tests.
- Study real `racing_dash/shared/vm` and `shared/channel_engine`; do not merely use the name 'VM' for an unrelated implementation.
- Channels: system / CAN decoded inputs / computed user channels; support deterministic arithmetic, comparisons, filters, timers, tables and alarms in bounded execution.
- Host preview must produce identical results to device implementation before allowing VM to control PDM outputs.
- Never let a user expression bypass current/temperature protections, fault latch or safety state.
- Avoid unsafe eval/exec of untrusted expressions. Include bytecode validation, limits and fuzz tests.

### 4. Legacy Cortex and QA
Own `Services/ConfigFileSerializer.cs`, related .NET tests, `.github/workflows/` and shared test documentation.
- Reject invalid config files, over-limit current thresholds and invalid indices; don't silently coerce dangerous settings.
- Run `dotnet build tests/Cortex.Tests/Cortex.Tests.csproj -c Release` and `dotnet test ... --collect:'XPlat Code Coverage'`.
- Run `python -m unittest discover -s r2_pdm_configurator/tests -v`.
- Run offscreen PyQt GUI smoke tests and add functional protocol simulation.
- Do not disable tests to obtain green CI. Verify any available artifact and note absence of hardware testing.

## Operational safety
The newly created CAN configurator is a prototype. A successful send is not an ACK.
A 0x701 status response does not identify the channel requested, so delayed frames can be misattributed.
Never claim full configuration sync or 'ready-to-use' until firmware/protocol supports stable transactions and bench tests confirm behavior.
No production builds/flash upgrades should occur without on-device validation.
