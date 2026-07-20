# Arty Z7 companion

Raspberry Pi 5 sitting next to the Xilinx Arty Z7 board (digit-recognition
demo lab). Drives an HDMI slideshow of MNIST test digits / user drawings
(via `feh`), reads the board's LEDs over GPIO, and streams pixel data to
the FPGA over UART for inference. CT300's `arty_lab_overlay/hardware.py`
is the client for this server.

## Wire protocol

TCP server on port 20000, 8-byte commands (space-padded), echoed back
once handled unless noted otherwise.

| Command | Effect |
|---|---|
| `img_next`/`img_last`/`img_home`/`img_end_` | feh slideshow navigation (virtual RIGHT/LEFT/HOME/END) |
| `button00`/`button01` | BTN0 (reset) off/on |
| `button10`/`button11` | BTN1 (start inference) off/on |
| `led_read` | Replies with the 6 LED GPIO pins as a `'{}{}{}{}{}{}'`-formatted string (no echo of the command itself) |
| `pick_img` | Reads one more byte (0-9), sends it as a single UART byte to select a preset image already baked into the FPGA's own ROM. Replies `ok_index`/`err_idx ` |
| `chunkimg` | Reads a 4-byte length prefix + that many PNG bytes, decodes to 784 raw pixels, sends **one byte at a time** over UART with a read-back/retry check after each byte (see below). Replies `ok_chnk `/`err_chnk` |
| `send_img` | Same PNG receive/decode as `chunkimg`, but sends the 784 pixel bytes as a single burst instead of one at a time. Replies `ok_send`/`err_img` |
| `del_drwn` | Deletes the current drawn-digit image and reloads feh. Replies `ok_deld` |

For `chunkimg`/`send_img`, the received PNG is also saved to
`/home/pi/mnist_images/drawn_digit.png` and feh is told to reload (`pkill
-USR1 feh`) so the HDMI slideshow shows the drawing, independent of
whether/when it's actually sent to the FPGA.

### Why chunked transfer exists

The original single-burst `send_img`/`send_image_uart()` path was
unreliable for continuous transfers. Root cause (found via a per-byte
debug log): Linux's tty layer was doing `OPOST`/`ONLCR` translation on
the UART port, silently rewriting any pixel byte equal to `0x0A`
(newline) into two bytes on the wire - desyncing the FPGA's byte count
every time a `0x0A`-valued pixel occurred. `_open_uart()` now opens the
port in full raw mode (`termios2`/`BOTHER` ioctl, since RPi 5's RP1 UART
driver silently falls back to 9600 baud for any rate not in `pyserial`'s
predefined table - this board runs at a fixed 125000 baud) to prevent
any of that rewriting.

`chunkimg`'s one-byte-at-a-time send with a GPIO-based readback/retry
(`send_image_chunked()`) is the fully-verified path added on top of the
raw-mode fix, for cases where even single-burst reliability isn't
sufficient. `send_img`'s single burst is fine now that raw mode is in
place, and is kept as the simpler/faster path.

**Note:** `pick_img`/`chunkimg`/`send_img` all both update the HDMI
preview *and* immediately send to the FPGA over UART in one call. There
is currently no separate "update the preview only, don't touch the FPGA
yet" command in this script - if the portal side calls one expecting
that split behavior, it isn't implemented here yet.

## Hardware notes

- GPIO: `lgpio` (RPi 5 compatible; `RPi.GPIO`/`gpiozero`'s older pin
  factories don't support RPi 5's GPIO chip).
- UART: direct `/dev/ttyAMA2` at 125000 baud via `termios2`/`BOTHER`
  (see above) - `pyserial` cannot set this rate on RPi 5.
- Virtual keyboard: `evdev`'s `UInput`, same mechanism as the Cyclone IV
  companion.

## Deployment

```bash
sudo cp arty_io_interface.py /home/pi/arty_io_interface.py
sudo cp arty-lab.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now arty-lab.service
```

`mnist_images/` (`digit_0.png` .. `digit_9.png`) are the 10 preset test
images shown in the portal's input gallery - copy them to
`/home/pi/mnist_images/` on the Pi.
