# fpga-remote-lab-pi-companions

Companion scripts for the Raspberry Pi that sits next to each FPGA board in
H-BRS's FPGA Vision Remote Lab. Each Pi runs a small TCP server that the
portal's backend (the `fpga-remote-lab` hardware repo, running on CT300)
talks to over the local network - it's the thing that actually presses
virtual buttons, flips GPIO switches, drives the input-image slideshow
shown over HDMI, and (where applicable) streams data to the FPGA over
UART.

These scripts don't live in the main hardware repo because they run on a
*different* machine (the Pi, not CT300) and are versioned/deployed
independently.

## Layout

One folder per lab board, each self-contained:

```
<board>/
  io_interface.py (or <board>_io_interface.py)   - the TCP server itself
  *.service                                        - systemd units that run it
  README.md                                        - board-specific notes
  (optional extra assets, e.g. test images)
```

| Folder | Board | Status |
|---|---|---|
| [`arty-z7/`](arty-z7/) | Xilinx Arty Z7 (digit-recognition demo) | Live |
| [`cyclone-iv/`](cyclone-iv/) | Altera/Intel Cyclone IV (EduPow 2.1, "Board 10") | Live |
| [`cyclone-x/`](cyclone-x/) | Cyclone X | Not yet connected |
| [`cyclone-v/`](cyclone-v/) | Cyclone V | Not yet connected |

Each board's own README documents its specific TCP command protocol,
hardware pinout, and deployment notes - see the folder itself.
