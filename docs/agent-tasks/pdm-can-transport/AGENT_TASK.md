# Agent task: CAN adapters: transport resilience and reliable readback

**Priority:** P0

**Branch:** `agent/pdm-can-transport`

**Owned paths:** r2_pdm_configurator/pdm_can.py and tests, transports/**

## Tasks
1. Validate 11-bit Synapse protocol with DBC golden vectors
2. Exercise PCAN/SocketCAN/SLCAN and R2 Racing Dash ECUMaster bridge without DLL copies
3. Prevent stale/unattributed multiframe responses from being reported valid
4. Protect all writes and verify applied configuration via readback

## Definition of done
Fake-CAN tests, no auto-actuation, explicit physical hardware limitations, PR targeting feature/r2-pdm-can-configurator.

## Rules
Read root `AGENTS.md` first. Work only within assigned paths, add regression tests, keep existing wire/EEPROM ABI unless versioned. No force push, no merge to main, and no hardware flash without validation. This file is a work item; it does not mean an autonomous coding agent is already running.
