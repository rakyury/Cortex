# Agent task: R2 Racing Dash-style dockable PDM GUI and live telemetry

**Priority:** P1

**Branch:** `agent/pdm-ui-telemetry`

**Owned paths:** r2_pdm_configurator/app.py, ui/**, models/**, UI tests

## Tasks
1. Separate UI from device/protocol model
2. Implement channel tree, diagnostics, live plots and responsive connection UI
3. Expose per-channel stale/fault timestamps and unit labels
4. Add automated offscreen GUI tests

## Definition of done
Headless smoke, telemetry fixtures, zero unprompted output control, PR targeting feature/r2-pdm-can-configurator.

## Rules
Read root `AGENTS.md` first. Work only within assigned paths, add regression tests, keep existing wire/EEPROM ABI unless versioned. No force push, no merge to main, and no hardware flash without validation. This file is a work item; it does not mean an autonomous coding agent is already running.
