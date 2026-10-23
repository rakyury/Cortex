# ADC telemetry display

HAL mode adds 17 ADC rows after the existing 21 diagnostic rows. The device VM
result stays at row 20. Battery mV is displayed as V; total and individual IS
sense mA as A; MCU die milli-degrees Celsius as degrees Celsius. Three decimal
places preserve the wire resolution. The temperature label always identifies
the MCU die, not board, driver or coolant temperature.

The new wire frame is 0x584; existing 0x581 diagnostics and the VM source
whitelist are unchanged. Connecting to older firmware leaves ADC rows
unavailable until actual ADC frames arrive. No values are inferred from gate
states, and an OFF gate does not overwrite a nonzero sense reading with zero.

Quality 0 is a factory-calibrated diagnostic value. Quality 2 is a nominal board
calibration estimate: the numeric table is orange, the graph caption says
"Nominal calibration", and tooltips explain that this is not per-board
calibration. Individual and total currents are diagnostic IS-pin estimates,
not independently verified load currents, including while outputs remain OFF.
Quality 1 (unavailable), 3 (invalid/saturated/fault) and 4 (stale acquisition)
produce an em dash and a graph gap, never a numerical zero. Missing host samples,
expired host timestamps and a missing compatible heartbeat also suppress values.

ADC values cannot be selected or loaded as device VM inputs in this iteration.
The local computed/legacy behavior remains unchanged. This display and simulator
validation do not establish per-board calibration or authorize actuation.

The cross-repository CI also injects raw ADC/calibration fixtures through the
actual C conversion/link/VM and verifies their values and gaps in Qt. It pins
the paired firmware commit in `.github/workflows/pdm-hal-integration.yml` and
runs with ASan/UBSan. To reproduce locally, set `PDM_FIRMWARE_ROOT` to the
paired SynapsePDM checkout, then run:

```sh
python -W error::ResourceWarning -m unittest discover -s r2_pdm_configurator/integration -p 'test_*.py' -v
```

Offscreen regression coverage:

```sh
python -m unittest discover -s r2_pdm_configurator/ui/tests -v
```
