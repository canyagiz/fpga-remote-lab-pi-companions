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

## Deployment

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

The gallery directory needs the actual test images placed by hand
(`0_Testbild_FH.png`, `a_stripes0.bmp`, `b_stripes1.bmp`,
`c_stripes2.bmp`, `street_0.bmp` .. `street_7.bmp`, plus the
`zz_custom.jpg` placeholder seeded from a copy of `custom_neutral.jpg`)
- they're not included in this repo since they're just static demo
  content, not part of the companion script itself.
