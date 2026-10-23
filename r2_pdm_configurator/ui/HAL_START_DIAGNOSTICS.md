# HAL start dispatcher diagnostics

Select **STM32Cube HAL diagnostics** and connect. The **HAL start dispatcher ·
read only** dock also appears in the **View** menu. It passively displays complete,
validated scheduler snapshots received over CAN. Opening the dock or connecting
does not send a discovery request or a start/cancel command.

The profile and channel count come from validated device metadata. Available
snapshots can describe 14 or 24 channels without changing the existing legacy
configuration controls or HAL sensor table. Queue position, virtual state,
reservation and held start slot are separate columns. A later queued channel
is waiting behind the head, even if its stored reason is OK. A fault may retain
its current reservation and start slot while waiting for OFF confirmation.

Total and group budgets use amperes. Current is reported separately from the
reserved envelope. **UNCOMMISSIONED / UNAVAILABLE** means the device has not
advertised usable scheduler limits; slots, budgets and reservations are Unknown,
not trusted zeroes. No channel values are invented for an unavailable profile.

**SIMULATION FIXTURES** identifies host-generated scenarios and fictional
limits. Inhibit and output-OFF flags are reported device diagnostics, not proof
of electrical output state or authorization to energize loads. Hardware
actuation remains blocked pending HIL.

Only coherent fresh snapshots populate the table. If scheduler data ages out,
the table and budgets clear even while ordinary HAL heartbeats continue. A
disconnect or incompatible heartbeat also clears the view. Firmware without
the additional diagnostics shows a waiting/unavailable status.

The separate **Start dispatcher · simulation only** dock remains a local,
manual simulator. Its buttons do not control the HAL device; this read-only CAN
dock has no request, cancel, fault, reset or output-control buttons.
