# Parallel Cortex / R2 PDM CAN configurator workboard

**Status:** Prepared branches and per-branch instructions only. Actual autonomous execution requires a connected coding agent / Work environment. GitHub Issues are disabled. Integration PR: #1 (draft), target `main`.

| Priority | Lane | Prepared branch |
|---|---|---|
| P0 | CAN adapters, protocol, confirmed readback | `agent/pdm-can-transport` |
| P1 | R2 Racing Dash-style GUI / telemetry | `agent/pdm-ui-telemetry` |
| P1 | VM/channel engine parity | `agent/pdm-channel-vm` |
| P0 | .NET/Python CI and coverage | `agent/cortex-qa-ci` |

Each branch has its own `AGENT_TASK.md` and inherits root `AGENTS.md`. Submit small PRs into `feature/r2-pdm-can-configurator`. Do not use unvalidated hardware writes and do not silently change wire compatibility.
