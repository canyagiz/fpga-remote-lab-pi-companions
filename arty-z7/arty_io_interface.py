#!/usr/bin/env python3
# TCP server for Arty Z7 remote lab.
# RPi 5 compatible: lgpio for GPIO, direct termios2 BOTHER ioctl for UART
# at exactly 125000 baud on GPIO4 (/dev/ttyAMA2). pyserial is not used
# because it silently falls back to 9600 on RPi 5's RP1 UART driver.

import lgpio
import socket
import time
import os
import fcntl
import struct
import termios
import subprocess
import evdev
from evdev import UInput, ecodes as e

try:
    from PIL import Image
    import io
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("WARNING: Pillow not available, image decoding will fail")

HOST = '0.0.0.0'
PORT = 20000

GPIO_CHIP  = 4
UART_BAUD  = 125000
UART_DEV   = '/dev/ttyAMA2'
IMAGE_SIZE = 784

PIN_BTN0 = 13
PIN_BTN1 = 19

PIN_LD0 = 12
PIN_LD1 = 6
PIN_LD2 = 16
PIN_LD3 = 20
PIN_LD4 = 5
PIN_LD5 = 26

INPUT_PINS = [PIN_LD0, PIN_LD1, PIN_LD2, PIN_LD3, PIN_LD4, PIN_LD5]

IMAGES_DIR       = '/home/pi/mnist_images/'
DRAWN_IMAGE_PATH = os.path.join(IMAGES_DIR, 'drawn_digit.png')

# ── termios2 BOTHER: set arbitrary baud rate on RP1 UART ─────────────────────
# Standard termios / pyserial / stty only accept predefined B* constants and
# silently fall back to 9600 for unknown values on RPi 5's rp1-uart driver.
# TCGETS2/TCSETS2 + BOTHER bypass this restriction and write the raw speed
# directly into the hardware divisor registers.
_TCGETS2 = 0x802C542A
_TCSETS2 = 0x402C542B
_BOTHER  = 0x1000
_CBAUD   = 0x100F | _BOTHER   # mask covering both old B* bits and BOTHER

def _open_uart(dev, baud):
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
    buf = bytearray(44)
    fcntl.ioctl(fd, _TCGETS2, buf)
    iflag = struct.unpack_from('<I', buf, 0)[0]
    oflag = struct.unpack_from('<I', buf, 4)[0]
    cflag = struct.unpack_from('<I', buf, 8)[0]
    lflag = struct.unpack_from('<I', buf, 12)[0]

    # Full raw mode. Without this, Linux's tty defaults (OPOST|ONLCR etc.)
    # silently rewrite certain bytes on the wire -- confirmed via the
    # chunked-transfer debug log: every failure occurred on exactly a pixel
    # value of 10 (0x0A / LF), with ONLCR expanding it into two bytes
    # (0x0D 0x0A), desyncing the FPGA's byte count by +1 every time it
    # occurred. This was very likely also a root contributor to the original
    # (pre-chunked) 784-byte burst transfer's reliability problems.
    iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
               termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
    oflag &= ~termios.OPOST
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)

    cflag = (cflag & ~_CBAUD) | _BOTHER
    cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
    cflag &= ~termios.PARENB
    cflag &= ~termios.CSTOPB

    struct.pack_into('<I', buf,  0, iflag)
    struct.pack_into('<I', buf,  4, oflag)
    struct.pack_into('<I', buf,  8, cflag)
    struct.pack_into('<I', buf, 12, lflag)
    struct.pack_into('<I', buf, 36, baud)   # c_ispeed
    struct.pack_into('<I', buf, 40, baud)   # c_ospeed
    fcntl.ioctl(fd, _TCSETS2, bytes(buf))
    return fd

_uart_fd = _open_uart(UART_DEV, UART_BAUD)

# ── lgpio GPIO setup ──────────────────────────────────────────────────────────
h = lgpio.gpiochip_open(GPIO_CHIP)

lgpio.gpio_claim_output(h, PIN_BTN0, 0)
lgpio.gpio_claim_output(h, PIN_BTN1, 0)

for pin in INPUT_PINS:
    lgpio.gpio_claim_input(h, pin, lgpio.SET_PULL_DOWN)

# Virtual keyboard for feh slideshow control
ui = UInput({e.EV_KEY: [e.KEY_SPACE, e.KEY_LEFT, e.KEY_RIGHT, e.KEY_HOME, e.KEY_END]},
            name='arty-lab-kbd')

# ── Helper functions ──────────────────────────────────────────────────────────
def press_key(key):
    ui.write(e.EV_KEY, key, 1)
    ui.syn()
    time.sleep(0.05)
    ui.write(e.EV_KEY, key, 0)
    ui.syn()

def set_pin(pin, value):
    lgpio.gpio_write(h, pin, value)

def read_leds():
    ld0 = lgpio.gpio_read(h, PIN_LD0)
    ld1 = lgpio.gpio_read(h, PIN_LD1)
    ld2 = lgpio.gpio_read(h, PIN_LD2)
    ld3 = lgpio.gpio_read(h, PIN_LD3)
    ld4 = lgpio.gpio_read(h, PIN_LD4)
    ld5 = lgpio.gpio_read(h, PIN_LD5)
    return '{}{}{}{}{}{}'.format(ld5, ld4, ld3, ld2, ld1, ld0)

def send_image_uart(raw_pixels):
    assert len(raw_pixels) == IMAGE_SIZE
    os.write(_uart_fd, raw_pixels)
    termios.tcdrain(_uart_fd)
    time.sleep(0.12)  # Wait for FPGA to receive all 784 bytes (784*10bits/125000 = 62.7ms)

def send_image_index(index):
    # ROM-based image loading: send a single byte (0-9) selecting a preset
    # image already baked into the FPGA's own ROM, instead of streaming
    # 784 raw pixel bytes over UART.
    assert 0 <= index <= 9
    os.write(_uart_fd, bytes([index]))
    termios.tcdrain(_uart_fd)
    time.sleep(0.001)

def read_led_raw_int():
    """Read the 6 LED GPIO pins as a single integer (bit5=LD5 ... bit0=LD0)."""
    ld0 = lgpio.gpio_read(h, PIN_LD0)
    ld1 = lgpio.gpio_read(h, PIN_LD1)
    ld2 = lgpio.gpio_read(h, PIN_LD2)
    ld3 = lgpio.gpio_read(h, PIN_LD3)
    ld4 = lgpio.gpio_read(h, PIN_LD4)
    ld5 = lgpio.gpio_read(h, PIN_LD5)
    return (ld5 << 5) | (ld4 << 4) | (ld3 << 3) | (ld2 << 2) | (ld1 << 1) | ld0

def send_image_chunked(raw_pixels, max_retries=5):
    """Send a 784-byte image one pixel at a time. After each byte, verify
    the FPGA's pixel counter (exposed on LD0-5, mod 64) actually advanced
    as expected before sending the next byte; retry (resend the same byte)
    on mismatch. Replaces the old single-burst 784-byte UART transfer,
    which proved unreliable for long continuous streams."""
    assert len(raw_pixels) == IMAGE_SIZE  # 784

    # Pulse reset to zero the FPGA's pixel counter before starting
    set_pin(PIN_BTN0, 1)
    time.sleep(0.01)
    set_pin(PIN_BTN0, 0)
    time.sleep(0.01)

    for expected_idx in range(IMAGE_SIZE):
        target_byte = raw_pixels[expected_idx]
        # RTL special-cases the final pixel: instead of naturally continuing
        # to (IMAGE_SIZE % 64), it wraps pixel_idx straight to 0 (and sets
        # image_ready) to avoid writing past fmap0's declared bounds. Match
        # that special case here, or the last byte's ACK check always fails.
        if expected_idx == IMAGE_SIZE - 1:
            expected_after = 0
        else:
            expected_after = (expected_idx + 1) % 64
        for attempt in range(max_retries):
            os.write(_uart_fd, bytes([target_byte]))
            # No tcdrain() here: measured at ~8ms/call on the RPi 5's RP1
            # UART driver (unrelated to actual transfer time, which is
            # ~80us for one byte at 125000 baud) -- was the entire
            # bottleneck of the chunked transfer (784 x 8ms = ~6.3s).
            # The 1ms sleep below already leaves more than enough margin
            # for the byte to clear the FIFO before the next write, so it
            # doubles as the drain wait.
            time.sleep(0.001)
            actual = read_led_raw_int() & 0x3F
            if actual == expected_after:
                break
        else:
            raise RuntimeError(
                "pixel {} gonderilemedi ({} deneme sonrasi)".format(expected_idx, max_retries))

    return True

def decode_png_to_pixels(png_bytes):
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow not installed on RPi")
    img = Image.open(io.BytesIO(png_bytes)).convert('L')
    img = img.resize((28, 28), Image.LANCZOS)
    import numpy as np
    # Scale to [0,127]: matches train.py np.round(img * 127) quantization
    pixels = np.round(np.array(img, dtype=np.float32) * (127.0 / 255.0)).astype(np.uint8)
    return bytes(pixels.flatten())

def reload_feh():
    try:
        subprocess.run(['pkill', '-USR1', '-x', 'feh'], capture_output=True)
    except Exception as ex:
        print("feh reload error: {}".format(ex))

# ── Command handler ───────────────────────────────────────────────────────────
def control_board(command, conn):
    cmd = command.decode('utf-8', errors='ignore').strip()

    if cmd == 'img_next':
        press_key(e.KEY_RIGHT)
    elif cmd == 'img_last':
        press_key(e.KEY_LEFT)
    elif cmd == 'img_home':
        press_key(e.KEY_HOME)
    elif cmd == 'img_end_':
        press_key(e.KEY_END)

    elif cmd == 'button00':
        set_pin(PIN_BTN0, 0)
    elif cmd == 'button01':
        set_pin(PIN_BTN0, 1)

    elif cmd == 'button10':
        set_pin(PIN_BTN1, 0)
    elif cmd == 'button11':
        set_pin(PIN_BTN1, 1)

    elif cmd == 'led_read':
        conn.sendall(read_leds().encode())
        return

    elif cmd == 'chunkimg':
        size_data = b''
        while len(size_data) < 4:
            chunk = conn.recv(4 - len(size_data))
            if not chunk:
                break
            size_data += chunk
        img_size = struct.unpack('>I', size_data)[0]
        img_data = b''
        while len(img_data) < img_size:
            chunk = conn.recv(min(4096, img_size - len(img_data)))
            if not chunk:
                break
            img_data += chunk

        with open(DRAWN_IMAGE_PATH, 'wb') as f:
            f.write(img_data)
        reload_feh()

        try:
            raw_pixels = decode_png_to_pixels(img_data)
            send_image_chunked(raw_pixels)
            conn.sendall(b'ok_chnk ')
        except Exception as ex:
            print("Chunked UART send error: {}".format(ex))
            conn.sendall(b'err_chnk')
        return

    elif cmd == 'pick_img':
        idx_byte = conn.recv(1)
        if not idx_byte or idx_byte[0] > 9:
            conn.sendall(b'err_idx ')
            return
        send_image_index(idx_byte[0])
        conn.sendall(b'ok_index')
        return

    elif cmd == 'send_img':
        size_data = b''
        while len(size_data) < 4:
            chunk = conn.recv(4 - len(size_data))
            if not chunk:
                break
            size_data += chunk
        img_size = struct.unpack('>I', size_data)[0]
        img_data = b''
        while len(img_data) < img_size:
            chunk = conn.recv(min(4096, img_size - len(img_data)))
            if not chunk:
                break
            img_data += chunk

        with open(DRAWN_IMAGE_PATH, 'wb') as f:
            f.write(img_data)
        reload_feh()

        try:
            raw_pixels = decode_png_to_pixels(img_data)
            send_image_uart(raw_pixels)
            conn.sendall(b'ok_send')
        except Exception as ex:
            print("UART send error: {}".format(ex))
            conn.sendall(b'err_img')
        return

    elif cmd == 'del_drwn':
        if os.path.exists(DRAWN_IMAGE_PATH):
            os.remove(DRAWN_IMAGE_PATH)
            reload_feh()
        conn.sendall(b'ok_deld')
        return

    conn.sendall(command)

# ── TCP server ────────────────────────────────────────────────────────────────
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(5)
print("Arty Z7 lab server listening on {}:{}".format(HOST, PORT))

while True:
    conn, addr = server.accept()
    try:
        data = conn.recv(8)
        if data:
            control_board(data, conn)
    except Exception as ex:
        print("Error: {}".format(ex))
    finally:
        conn.close()
