# Local computed channels

Use the **Computed channels · local preview** dock (available in the View menu)
to add up to 16 named computed channels. Choose a source, an operation and
either another source or an integer constant. Add creates a channel; select
an existing definition in the list to Update or Remove it.

Example: select Voltage, Less than, Constant 11500 and name the channel
"Low battery". The result is 1 below 11.5 V, otherwise 0. Voltages use mV and
currents use mA to retain integer precision in the R2 VM. Temperatures use
integer degrees Celsius; analogue values use thousandths of the displayed unit.
Constants use the selected source's units. Arithmetic/comparisons between two
sources require matching units. Comparisons return 0/1; Add/Subtract overflow
is invalid rather than displayed as a wrapped engineering value.

Computed rows appear after the physical telemetry rows and under **COMPUTED ·
LOCAL** in the channel tree. Select one to use the existing graph. Editing a
definition resets graph history. Results require every source to have fresh,
valid telemetry. Missing, stale, degraded or faulted sources produce no numeric
result and a graph gap; the preview never substitutes zero for missing input.

Save local / Load local export/import a versioned JSON definition file. Loading
validates the entire bounded file before replacing any definitions. Definitions
remain in memory across disconnects; after reconnect they wait for fresh data.
They are not loaded automatically at startup.

The editor builds bounded read-only bytecode and runs the existing R2 VM preview
on immutable scaled snapshots. It receives no transport/client and cannot send
CAN frames, modify configuration, upload programs or bind results to outputs.
All results are diagnostic: the legacy CAN response limitations still apply.
STM32Cube physical outputs remain OFF until separate HIL validation.

Tests: normal unittest discovery includes the Qt-free computed model tests.
Run `python -m unittest discover -s r2_pdm_configurator/ui/tests -v` for the
offscreen editing, graph, freshness, file roundtrip and zero-write tests.
