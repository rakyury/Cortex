# Agent task: Racing Dash VM parity for computed PDM telemetry channels

**Priority:** P1

**Branch:** `agent/pdm-channel-vm`

**Owned paths:** r2_pdm_configurator/vm/** and channel_engine/** and tests

## Tasks
1. Compare R2 Racing Dash shared/vm/vm.c + shared/channel_engine interfaces
2. Implement bounded math, comparisons, channels, filters, timers and alarms
3. Use golden vectors proving host/firmware parity
4. Guarantee safety interlock cannot be bypassed by user expressions

## Definition of done
Determinism/overflow/fuzz tests, execution budget, versioned VM proposal, PR targeting feature/r2-pdm-can-configurator.

## Rules
Read root `AGENTS.md` first. Work only within assigned paths, add regression tests, keep existing wire/EEPROM ABI unless versioned. No force push, no merge to main, and no hardware flash without validation. This file is a work item; it does not mean an autonomous coding agent is already running.
