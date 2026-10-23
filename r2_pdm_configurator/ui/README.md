# Telemetry presentation validation

The immutable model has no Qt or CAN I/O dependency. Its tests run in the
normal Python suite. Qt controls and drawing are validated separately by the
PR workflow `PDM telemetry UI offscreen tests`, which installs PyQt6 and native
Qt offscreen libraries. Both suites are required; neither uses skipped tests.

```sh
python -m unittest discover -s r2_pdm_configurator/tests -v
QT_QPA_PLATFORM=offscreen python -m unittest discover -s r2_pdm_configurator/ui/tests -v
```

On PowerShell, set `$env:QT_QPA_PLATFORM='offscreen'` before the second command.

The model never sends CAN commands. Freshness limits are 1 second for primary
system heartbeat, 7 seconds for each output, 6 seconds for each analogue input,
and 2 seconds for digital inputs. The output/analogue limits allow a full
serialized polling cycle. Missing timestamps, old samples, backward clocks,
nonfinite values, and lost heartbeat remove a value from live display/trend;
the tooltip retains its timestamp and last sample for diagnosis.

These are display freshness limits, not firmware protection deadlines.
Legacy replies are uncorrelated diagnostics, and a configuration readback match
is not an acknowledgement. Digital freshness and persistent readback details
use the transport lane's optional metadata; older clients lacking a digital
receive timestamp display that input as unavailable.
