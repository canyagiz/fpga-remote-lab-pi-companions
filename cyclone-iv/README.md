# Cyclone IV companion (Board 10, EduPow 2.1)

Raspberry Pi (Model B+, armv6, Raspberry Pi OS Trixie / Python 3.13) sitting
next to the Cyclone IV board. It doesn't talk to the FPGA directly - it
drives a slideshow of test images over HDMI (captured by a Magewell
capture card into the portal's video feed) and reports/toggles a few GPIO
lines. CT300's `c_x_lab_overlay/hardware.py` is the client for this
server.

Modernized rewrite of an older Python 2 script (`uinput` + `RPi.GPIO`,
neither installable on a current OS). Wire protocol is unchanged from the
original on purpose.

## Wire protocol

TCP server on port 20000. Every command is exactly 8 bytes (space-padded),
sent as-is and echoed back as an ack once handled - except `img_upld`,
which additionally reads a 4-byte big-endian length prefix and that many
bytes of raw JPEG data before acking.

| Command | Effect |
|---|---|
| `img_next` | Advance the feh slideshow one image (virtual SPACE key) |
| `img_last` | Go back one image (virtual LEFT key) |
| `img_home` | Reload + jump to the first image (virtual R, then HOME) |
| `img_end_` | Jump to the last image (virtual END) |
| `img_upld` | Receive an uploaded JPEG (see below), write it into the gallery's custom slot, reload + jump there |
| `img_copy` | Restore the neutral "no custom image" placeholder over the custom slot + jump home - the session-reset command |
| `switch00`/`switch01` | enable_in(0) off/on |
| `switch10`/`switch11` | enable_in(1) off/on |
| `switch20`/`switch21` | enable_in(2) off/on |

### Custom image upload (`img_upld`/`img_copy`)

The gallery directory (`/home/pi/Desktop/pics/`) is a fixed set of files
that `feh` (the slideshow viewer) lists once at startup and never
rescans. To support user-uploaded images without restarting the
slideshow, one file in that directory - `zz_custom.jpg` (sorts last
alphabetically) - is a permanent placeholder slot whose *contents* get
swapped, never the file itself:

- `img_upld` overwrites `zz_custom.jpg` with the uploaded JPEG, then
  presses `R` (force feh to reload this filename from disk instead of
  showing a cached decode) followed by `END` (jump to this, the last,
  slide).
- `img_copy` overwrites `zz_custom.jpg` with a neutral "no custom image
  yet" placeholder (kept at `/home/pi/custom_neutral.jpg`, outside the
  gallery dir so feh never lists it separately) and sends the slideshow
  home. The portal calls this at the start (and end) of every lab
  session so one user's upload can never leak into the next user's
  session.

## Hardware notes

- Virtual keyboard: `evdev`'s `UInput` (kernel-level input device) rather
  than X11-specific injection, so key presses reach Wayland's `libinput`
  too (this Pi runs `labwc`, a Wayland compositor, by default on this OS
  version).
- GPIO: `gpiozero` (auto-selects a modern pin factory) rather than
  `RPi.GPIO`, which doesn't support this OS/kernel's GPIO character
  device interface.
- Physical next/prior buttons on the board are wired the same way the
  TCP commands are (same `press_key` calls), so they work identically
  whether driven remotely or in person.

### HDMI output resolution (must be forced to 1280x720)

The Magewell capture card's EDID (the capability block an HDMI sink
sends the source, listing supported modes and its preferred one)
advertises 1920x1080 as preferred. With nothing overriding that, labwc
(the Wayland compositor this Pi runs) negotiates 1080p on every login -
even though the gallery images are all authored at 1280x720.

That mismatch is exactly what produced the "black frame / 3-way
tiled" corruption seen on Experiment 2 (sharpening) and Experiment 3
(lane detection): the Cyclone IV bitstream's image-processing IPs use
a fixed-width line buffer sized for 1280 columns. Fed a 1920-wide
signal, the buffer wraps early relative to the real scanline every
~1280 columns (1920/1280 = 1.5, matching the observed tiling).
Experiment 1 (inverting) has no line buffer, so it looked unaffected
and gave no hint the output resolution was wrong. `v4l2-ctl` on the
portal side showed nothing abnormal either, since the Magewell was
correctly capturing whatever the FPGA sent it - the fault was upstream
of the capture card, not in it.

**Forcing the kernel's `video=` cmdline parameter does not work** -
tried first, and confirmed (by rebooting and re-checking `wlr-randr`)
that labwc re-negotiates 1080p from EDID at compositor startup
regardless of what `cmdline.txt` says. The fix that actually persists
across reboots is a labwc autostart script -
[`labwc-autostart`](labwc-autostart) - which runs after the compositor
is already up and forces the mode explicitly:

```bash
sudo cp labwc-autostart /home/pi/.config/labwc/autostart
sudo chmod +x /home/pi/.config/labwc/autostart
```

Verify with `WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
wlr-randr` - the `1280x720` line should be marked `(current)`, not
`1920x1080`.

#### Second failure mode: connector reported as disconnected

The autostart script above only works if the kernel's DRM connector
(`/sys/class/drm/card0-HDMI-A-1/status`) says `connected` in the first
place - a mode forced onto a connector the kernel believes is
disconnected is refused outright, logged in `dmesg` as:

```
[drm] User-defined mode not supported: "1280x720": 60 74250 1280 1390 1430 1650 720 725 730 750 0x60 0x5
```

When that happens, `wlr-randr` shows no `HDMI-A-1` output at all - only
a synthetic headless fallback (`NOOP-1`, 1920x1080 only) - so the
autostart script's `--output HDMI-A-1` target silently matches nothing.

Seen in practice after a power cycle of the Pi: a hotplug-detection
race between the Pi coming up and the FPGA's HDMI receiver being ready
to respond can leave the connector marked disconnected for that boot,
even though the exact same cabling worked fine on the previous boot.

Fix: append [`config.txt.append`](config.txt.append) to
`/boot/firmware/config.txt`:

```bash
cat config.txt.append | sudo tee -a /boot/firmware/config.txt
```

This tells the VideoCore firmware to treat the port as connected
regardless of the live hotplug-detect signal, so EDID is read and the
forced mode is accepted every boot instead of depending on the timing
of that race. Reboot to apply, then verify both
`/sys/class/drm/card0-HDMI-A-1/status` (`connected`) and `wlr-randr`
(`1280x720 (current)`, real EDID modes listed instead of the
`NOOP-1` fallback).

## Setup from scratch

### 1. OS image and first boot

Flash **Raspberry Pi OS (Desktop image, not Lite)** - Trixie (Debian 13)
is what this was built/tested against. Using Raspberry Pi Imager's
"Edit Settings" (gear icon) before writing, set:

- Hostname, and enable SSH (password or key auth).
- **Enable auto-login to the desktop as user `pi`.** This is not
  optional: `civ-slideshow.service` depends on a real graphical session
  already being up (`graphical.target` alone isn't enough - a login
  screen sitting idle also satisfies that target without anyone ever
  being logged in). Without auto-login, feh and the virtual keyboard
  have no desktop session to attach to and the whole HDMI output chain
  stays dark. Imager's own auto-login option is sufficient; no manual
  `raspi-config` step needed if set there. Confirm afterwards with
  `systemctl get-default` (should say `graphical.target`) and checking
  `/etc/lightdm/lightdm.conf` for `autologin-user=pi`.

This Pi's desktop session is `labwc` (a Wayland compositor) - that's
Raspberry Pi OS Trixie's default, nothing extra to select.

### 2. Install packages

`python3-gpiozero` and `python3-lgpio` normally ship pre-installed on
the Desktop image; `feh` and `python3-evdev` do not and need installing
explicitly:

```bash
sudo apt update
sudo apt install feh python3-evdev python3-gpiozero python3-lgpio python3-pil
```

(`python3-pil` is needed by `make_custom_placeholder.py` below, not by
`io_interface.py` itself.)

### 3. Deploy the companion script + services

Two systemd units, both `WantedBy=graphical.target`:

- `civ-lab.service` - runs `io_interface.py` as root (GPIO access).
  Started `After=graphical.target` deliberately: creating the virtual
  keyboard device before the Wayland compositor is fully up means the
  compositor never picks it up, and key presses silently go nowhere.
- `civ-slideshow.service` - runs `feh` in gallery mode over the fixed
  image directory, as user `pi`, with `DISPLAY=:0` (feh is an X11 app,
  running via Xwayland on top of `labwc`).

```bash
sudo cp io_interface.py /home/pi/io_interface.py
sudo cp civ-lab.service civ-slideshow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now civ-lab.service civ-slideshow.service
```

Also deploy [`labwc-autostart`](labwc-autostart) (see [HDMI output
resolution](#hdmi-output-resolution-must-be-forced-to-1280x720) above) -
without it the board's video-processing experiments will show
corrupted output:

```bash
mkdir -p /home/pi/.config/labwc
cp labwc-autostart /home/pi/.config/labwc/autostart
chmod +x /home/pi/.config/labwc/autostart
```

Append [`config.txt.append`](config.txt.append) to
`/boot/firmware/config.txt` at the same time (see [second failure
mode](#second-failure-mode-connector-reported-as-disconnected) above) -
doing this at imaging time avoids ever hitting the disconnected-on-boot
race in the field:

```bash
cat config.txt.append | sudo tee -a /boot/firmware/config.txt
```

### 4. Gallery images

The gallery directory (`/home/pi/Desktop/pics/`) needs:

- The actual demo test images - `0_Testbild_FH.png`, `a_stripes0.bmp`,
  `b_stripes1.bmp`, `c_stripes2.bmp`, `street_0.bmp` .. `street_7.bmp`.
  These are static lab content (not generated, not part of the
  companion script) and aren't included in this repo - source them from
  wherever the lab's existing image set lives (e.g. copy from another
  already-running Cyclone IV Pi, or from the portal's own copy under
  `FPGA_Vision_Remote_Lab_experiment/Experiment_files/images/` in the
  `fpga-remote-lab` hardware repo).
- `zz_custom.jpg`, seeded as a copy of `custom_neutral.jpg` (see below) -
  this is the custom-upload slot, and unlike the images above it must
  sort alphabetically **last** in this directory (hence the `zz_`
  prefix) - see [Custom image upload](#custom-image-upload-img_upldimg_copy)
  above for why.

`custom_neutral.jpg` itself (referenced by `NEUTRAL_PATH` in
`io_interface.py`) is generated, not a static asset - run
[`make_custom_placeholder.py`](make_custom_placeholder.py) on the Pi to
create it, then seed the gallery slot from it:

```bash
python3 make_custom_placeholder.py   # writes /home/pi/custom_neutral.jpg
cp /home/pi/custom_neutral.jpg /home/pi/Desktop/pics/zz_custom.jpg
```

Re-run the script (and re-copy) any time you want to change what the
placeholder looks like - `io_interface.py`'s `img_copy` handler always
copies fresh from `custom_neutral.jpg`, so that file is the source of
truth, not the gallery copy.
