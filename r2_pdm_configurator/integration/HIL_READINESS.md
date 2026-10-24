# HIL preparation: fault scenarios and evidence boundaries

**Physical HIL has not been performed. Hardware outputs remain OFF and actuation
is blocked.** This document records software fault scenarios and the physical
evidence still missing. It contains no procedure for activating outputs.

The host harness compiles the actual C scheduler, ADC conversion model, VM and
CAN serializer. Python transports those simulated frames into the actual Cortex
decoder and, for `HIL-UI-*`, the offscreen Qt interface. Separate firmware tests
mock STM32 HAL calls and inspect software GPIO writes. Neither kind of test
proves an electrical output is OFF, measures a sensor, or exercises a physical
CAN transceiver. A reported gate value of zero is software evidence only.

## Reproduce and preserve software evidence

Use the paired SynapsePDM revision pinned in the cross-repository CI workflow,
with its `pdm_cube/tests/link_simulator.c`. Install the Python dependencies listed
in [README.md](README.md). From the Cortex repository root on a sanitizer-capable
Linux host:

```sh
export PDM_FIRMWARE_ROOT=/absolute/path/to/paired/SynapsePDM
export QT_QPA_PLATFORM=offscreen
export CC=gcc
export PDM_LINK_SANITIZE=1
python -W error::ResourceWarning -m r2_pdm_configurator.integration.run_hil_faults --output artifacts/hil-faults
```

The default pattern runs `test_hil_fault*.py`. Add `--pattern 'test_*.py'` to run
the complete integration suite, including existing ADC, VM and scheduler cases.
The runner writes `scenarios.json`, `junit.xml` and `tests.log`; skipped or empty
runs are incomplete and return a failure status. JSON records source revisions,
compiler, sanitizer selection, scenario outcomes and `hardware_tested=false` /
`hardware_authorized=false`. Preserve the CI job URL and full source revisions
with those files. A dirty local checkout needs its diff archived too; a Git SHA
alone does not identify its uncommitted test sources.

On Windows set the equivalent environment variables in PowerShell and ensure
the compiler DLL directory is on `PATH`. If the compiler lacks sanitizer
runtimes, omit `PDM_LINK_SANITIZE`; that run is explicitly weaker evidence, not a
substitute for the Linux ASan/UBSan job. Firmware-only mock tests run separately
with `python pdm_cube/tests/run_host_tests.py --cc gcc` from SynapsePDM.

## Scenario matrix

All injected commands below are private **host stdin fixtures**, not CAN control
messages or hardware instructions. Current values, budgets, durations and
profile 24 mapping are fictional simulator inputs. The 14-channel hardware
profile remains uncommissioned; absent budgets must appear Unknown. The
software test reference identifies an assertion to run, not a physical pass.

| ID | Software injection | Expected firmware/model and Cortex behavior; software pass criterion | Test reference | Physical proof still pending |
| --- | --- | --- | --- | --- |
| HIL-CAN-01 | Drop receiver frames **and separately** apply `STARTLINK 0`, then restore both; profiles 14/24, slots 2/3/4 | Receiver becomes offline; explicit scheduler link fault cancels queued requests and faults active channels while retaining reservations. Restoring link cannot replay canceled requests or clear faults. Consistent OFF proof, explicit clear and a new request are separate host actions. | [test_hil_faults.py](test_hil_faults.py): `test_blackout_and_explicit_link_fault_never_replay_queue` | Device-side link supervision from a real bus condition, transceiver behavior and electrical OFF. Dropping frames in Python does not establish firmware detection. |
| HIL-CAN-02 | Deliver VM BEGIN; drop reverse ACK/readback frames | No DATA/COMMIT follows an unacknowledged BEGIN; upload fails, remains unverified and disarmed. Restored replies do not retry the transaction. Software output diagnostics remain zero. | [test_hil_faults.py](test_hil_faults.py): `test_one_way_reply_loss_does_not_retry_upload_on_recovery` | Physical asymmetric communication faults, controller timing and output observation. |
| HIL-CAN-03 | Mix a delayed old-generation DATA fragment into a new profile transfer | Reject the mixed snapshot. Only a coherent later generation can restore profile, queue and reservations. Heartbeat alone cannot validate it. | [test_hil_faults.py](test_hil_faults.py): `test_delayed_old_generation_cannot_mix_with_new_profile` | Adapter buffering/reordering under real traffic, physical CAN load timing. |
| HIL-RESET-01 | Reset host firmware while VM DATA is pending; deliver old DATA afterward | Staging and client authorization are discarded. No automatic upload continuation; VM is unavailable and profile 14 returns as uncommissioned metadata. | [test_hil_faults.py](test_hil_faults.py): `test_reset_mid_upload_cannot_resume_old_transaction` | MCU reset, watchdog and brownout paths, actual reset timing and gate levels. |
| HIL-SENSOR-01 | Freeze scheduler input timestamps, then resume them | Fresh CAN snapshot reports current Unknown/inhibited when input data is stale; FAULT reservations remain. Measurement recovery does not clear the latched channel. | [test_hil_faults.py](test_hil_faults.py): `test_frozen_measurement_fault_stays_latched_after_sensor_recovery` | Physical acquisition stalls and measured end-to-end protection timing. |
| HIL-SENSOR-02 | Current above the channel's fictional reserved envelope; then OFF flag with nonzero current | Latch FAULT and retain reservation even when total budget has room. Contradictory OFF feedback cannot release the reserve or clear the fault. | [test_hil_faults.py](test_hil_faults.py): `test_excess_current_and_contradictory_off_never_release_reservation` | Sensor calibration, real OFF feedback semantics, wiring faults and independent current measurement. |
| HIL-SENSOR-03 | Independent total-current fixture exceeds reserved envelope but remains below total budget | Veto new admission and retain existing FAULT reserve. The queued request has an unhealthy reason. The test explicitly cancels it before restoring the meter; it does not claim health recovery cancels pending requests. | [test_hil_faults.py](test_hil_faults.py): `test_independent_total_above_reserve_blocks_admission_below_budget` | Real total-current sensing, unexplained load detection and approved limits. |
| HIL-SENSOR-04 | Invalidate a channel sensor or its group health; attempt early fault clear | Fault remains latched until required health and consistent fresh OFF proof exist. Explicit clear remains separate from a new request. | [test_hil_faults.py](test_hil_faults.py): `test_invalid_channel_and_group_health_cannot_be_cleared_early` | Board group assignment, physical sensor validity and fault signaling. |
| HIL-ADC-01 | Saturated current raw value, invalid reference, then old acquisition timestamp | ADC quality becomes invalid or stale; no fabricated numerical zero. Nominal fresh samples recover. The separately supplied scheduler fixture is not treated as real ADC feedback. | [test_hil_faults.py](test_hil_faults.py): `test_adc_saturation_invalid_and_stale_are_separate_from_scheduler_fixture` | Actual ADC source/mux behavior, acquisition recovery, reference and per-board calibration. |
| HIL-UI-01 | Healthy current, saturated raw current, then a different healthy raw value | Current and total graph/table values become gaps; unrelated voltage remains available. Fresh recovery shows the new nominal estimate, and the earlier graph gap remains. Passive UI sends no CAN messages. | [test_hil_fault_ui.py](test_hil_fault_ui.py): `test_current_saturation_gap_then_new_value_recovery` | Physical source injection and independent measurements; nominal estimates are not verified load current. |
| HIL-UI-02 | Freeze scheduler timestamps while continually providing fresh ADC fixtures, then resume scheduler inputs | Scheduler current becomes Unknown while held FAULT reservations remain visible; ADC estimates remain fresh. Recovery restores numerical scheduler current without changing the latched channel into a start. | [test_hil_fault_ui.py](test_hil_fault_ui.py): `test_frozen_scheduler_current_is_unknown_while_adc_stays_fresh` | Independence and timing of physical acquisition paths and real output state. |
| HIL-UI-03 | Corrupt snapshot CRC; hold END during recovery; finally allow a complete new profile | Old rows/budgets disappear after rejection. Partial recovery cannot repopulate them. Complete recovery restores the advertised profile and simulation label; the fixed 38-row HAL sensor table remains intact. | [test_hil_fault_ui.py](test_hil_fault_ui.py): `test_crc_fault_clears_old_rows_until_complete_recovery` | Physical adapter error/loss behavior and operator-visible timing on target hardware. |
| HIL-HAL-01 | Mock HAL CAN BOF/FOV0 error reports, RX failures and prolonged TX mailbox starvation | Diagnostic transitions are counted, RX work stays bounded, and foreground processing continues. Actual-main mock test preserves software OFF writes during failed TX. | SynapsePDM `pdm_cube/tests/test_can_monitor.c`, `test_main_outputs.c` | **Physical bus-off detection/recovery is unproven. The current polling target has no installed CAN error IRQ handler.** Mock `HAL_CAN_GetError` does not establish how real errors reach that function. |
| HIL-HAL-02 | Mock ADC init/config/start/poll failures, overrun, scan/conversion timeout and mux faults | Acquisition failures stay invalid and cannot be interpreted as zero or fresh numerical readings. Conversion-model tests cover saturation, reference and calibration boundaries separately. | SynapsePDM `pdm_cube/tests/test_adc_hal.c`, `test_adc.c` | Actual analog faults, HAL acquisition reinitialization/recovery and measured timing. A fresh host `ADC` command only demonstrates model recovery. |
| HIL-HAL-03 | Host scheduler link, group/global health, inhibit, sensor-validity and parser-boundary fixtures across profiles/slot limits | Strict bounded input parsing; no partial invalid command application; fault reserves and OFF-proof rules hold. Pending demand follows the policy described below. | SynapsePDM `pdm_cube/tests/test_link_faults.c` and `pdm_cube/docs/hil-fault-fixtures.md` | Commissioned hardware mapping, independently approved thresholds and electrical OFF evidence. |

## Recovery policy and interpretation

CAN visibility and scheduler link health are distinct in these tests.
`STARTLINK` supplies a fictional scheduler liveness input. Receiver blackout only
tests what Cortex can see; it does not connect a real transport timeout to
device-side scheduler protection. Likewise, ADC diagnostics and scheduler
current feedback are independent host fixtures. Neither is commissioned for
physical output control.

Link loss cancels queued requests. Other transient health, inhibit or sensor
faults can leave unexpired requests queued, and those pending requests may be
admitted when their conditions recover. A latched active channel remains FAULT
until explicit recovery; restoring health alone does not clear that fault.
Tests and reports must preserve this distinction instead of claiming all
recovery prevents admission of existing queued demand.

Input TTLs and decoder freshness limits are software rules, not validated
hardware protection response times. Gate OFF does not force the nominal IS
current estimate to zero. MCU die temperature is not driver, board or load
temperature. UI nominal calibration warnings must remain visible after recovery.

## Physical evidence required before any readiness claim

Every physical item below is **pending**. This list defines missing evidence,
not an activation procedure or authorization to run hardware.

- Identify the board revision, validated pinout, component population, sensor
  circuits and transceiver. Profile 24 is simulation-only. Supply conditions,
  current ratings, pulse durations, thermal limits and allowed fault injection
  levels require separate hardware review; no supply voltage or rating is
  assumed here.
- Obtain independent gate/output observations through startup, reset, watchdog,
  brownout and communication/sensor faults with the OFF interlock maintained.
  Software telemetry and mock GPIO calls cannot substitute for that observation.
- Verify the actual bxCAN error-reporting path, interrupt/polling behavior,
  physical bus-off and recovery, FIFO pressure and mailbox starvation. Record
  both electrical/adapter traces and corresponding firmware diagnostics.
- Verify sensor/reference/mux fault detection and recovery with a separately
  approved bench method, known calibration and independent measurements. Specify
  acceptable response times before testing rather than deriving them from a
  passing host fixture.
- Preserve exact firmware/Cortex revisions, board identity, fixture description,
  instrument identity/calibration, operator, timestamps, traces and declared pass
  criteria. A software CI artifact can support that record but cannot close a
  physical evidence item.

The checklist remains open even if every software scenario passes. No output
activation, flashing or physical HIL is performed by these tests or their runner.
