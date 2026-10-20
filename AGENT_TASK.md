# Agent task: Cortex + PyQt QA, coverage and integration fixtures

**Priority:** P0

**Branch:** `agent/cortex-qa-ci`

**Owned paths:** tests/**, r2_pdm_configurator/tests/**, .github/workflows/**

## Tasks
1. Maintain .NET build + xUnit tests with coverage
2. Run python-can fixtures and end-to-end fake PDM simulation
3. Expand PyQt offscreen scenarios, including disconnects and malformed CAN
4. Publish results as artifacts; keep both legacy USB and new CAN clients regression-safe

## Definition of done
Green CI and measurable test coverage, no ignored failures, PR targeting feature/r2-pdm-can-configurator.

## Rules
Read root `AGENTS.md` first. Work only within assigned paths, add regression tests, keep existing wire/EEPROM ABI unless versioned. No force push, no merge to main, and no hardware flash without validation. This file is a work item; it does not mean an autonomous coding agent is already running.
