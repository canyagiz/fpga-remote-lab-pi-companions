# Cyclone X companion

Not yet connected/testable - `fpga-lab-cx` on CT300 exists and is
configured to talk to a Pi at this board's UART host, but no Raspberry
Pi has been set up for it yet. This folder will get the same treatment
as [`cyclone-iv/`](../cyclone-iv/) once the board is physically wired
up: a `io_interface.py` (likely a near-copy of Cyclone IV's, adjusted
for whatever's different about this board's GPIO/image setup), its
systemd units, and a README documenting the protocol.
