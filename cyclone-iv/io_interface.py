#!/usr/bin/env python3
# TCP server for the Cyclone IV remote lab - Raspberry Pi Model B+ (armv6),
# Raspberry Pi OS Trixie / Python 3.13.
#
# Modernized rewrite of the original Python 2 io_interface.py, which used
# the `uinput` package and RPi.GPIO (neither maintained/installable on a
# current OS). Wire protocol is unchanged on purpose - this is a drop-in
# replacement, CT300's hardware.py/config_exp_civ.py talk to this over the
# exact same TCP port with the exact same 8-byte commands.
#
# - Virtual keyboard: evdev's UInput (kernel-level input device, same
#   mechanism the old uinput package used) instead of X11-specific input
#   injection - this makes key presses visible to Wayland's libinput too,
#   since libinput reads the same /dev/input evdev nodes X11 did.
# - GPIO: gpiozero (auto-selects a modern pin factory) instead of
#   RPi.GPIO, which doesn't support this OS/kernel's GPIO character
#   device interface.
#
# Commands NOT carried over from the original: play_vid/play_vi2/play_vi3
# (video playback) - CT300's hardware.py never sends these.
#
# img_upld / img_copy: the custom-image-upload pair, re-implemented for the
# feh-based display. img_upld receives a user's image and shows it;
# img_copy restores the neutral placeholder over it. img_copy is the
# session-scoped reset - CT300's on_start AND on_dispose both send it (see
# hardware.py), so one user's uploaded image is wiped before the next user
# ever sees the lab. feh builds its file list once at startup and never
# rescans, so the custom slot (zz_custom.jpg) must already exist in the
# gallery directory before feh launches; we only ever overwrite its
# *contents*, never add/remove the file, and press R to force feh to
# re-read it from disk.

import socket
import time
import shutil

from evdev import UInput, ecodes as e
from gpiozero import OutputDevice, Button

HOST = '0.0.0.0'
PORT = 20000

# The last (alphabetically) file in the gallery dir, so it's feh's final
# slot. Its contents get overwritten per upload / reset; the file itself
# is never added or removed at runtime.
CUSTOM_SLOT_PATH = '/home/pi/Desktop/pics/zz_custom.jpg'
# Pristine "No custom image yet" placeholder, copied over the slot on
# every session reset. Lives outside the gallery dir so feh never lists
# it as a separate image.
NEUTRAL_PATH = '/home/pi/custom_neutral.jpg'

# BCM pin numbers - the original script used RPi.GPIO's physical BOARD
# numbering (GPIO.setmode(GPIO.BOARD)); these are the BCM-numbered
# equivalents of the same physical pins, since gpiozero addresses pins by
# BCM number.
SWITCH_PINS = {
    0: 4,   # physical pin 7
    1: 17,  # physical pin 11
    2: 27,  # physical pin 13
}
BTN_NEXT_PIN = 24   # physical pin 18
BTN_PRIOR_PIN = 23  # physical pin 16

switches = {n: OutputDevice(pin, initial_value=False) for n, pin in SWITCH_PINS.items()}

ui = UInput({e.EV_KEY: [e.KEY_SPACE, e.KEY_LEFT, e.KEY_HOME, e.KEY_END, e.KEY_R]},
            name='civ-lab-kbd')


def press_key(key):
    ui.write(e.EV_KEY, key, 1)
    ui.syn()
    time.sleep(0.05)
    ui.write(e.EV_KEY, key, 0)
    ui.syn()
    # Give labwc/Xwayland time to actually deliver this key event to feh
    # (change the displayed image, redraw) before the next one can be
    # injected - two SPACE presses sent back-to-back with no gap between
    # them were observed to only advance the gallery once, since the
    # second key event landed while the compositor was still processing
    # the first one's consequences.
    time.sleep(0.15)


def next_pic():
    press_key(e.KEY_SPACE)


def prior_pic():
    press_key(e.KEY_LEFT)


# Physical buttons on the board, mirroring the same feh navigation the TCP
# commands trigger - present in the original script too.
_btn_next = Button(BTN_NEXT_PIN, pull_up=True, bounce_time=0.3)
_btn_next.when_pressed = next_pic
_btn_prior = Button(BTN_PRIOR_PIN, pull_up=True, bounce_time=0.3)
_btn_prior.when_pressed = prior_pic


def recv_exact(conn, n):
    """recv() may return fewer bytes than requested in a single call - loop
    until exactly n have arrived. A short close mid-transfer raises rather
    than silently writing a truncated image."""
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('connection closed mid-transfer')
        buf += chunk
    return buf


def receive_image(conn):
    # Protocol after the 8-byte 'img_upld' command: a 4-byte big-endian
    # length prefix, then exactly that many bytes of JPEG data. Written to
    # the pre-existing custom slot so feh already knows the filename.
    length = int.from_bytes(recv_exact(conn, 4), 'big')
    data = recv_exact(conn, length)
    with open(CUSTOM_SLOT_PATH, 'wb') as f:
        f.write(data)
    # R reloads the current file from disk (feh caches the decoded image
    # otherwise, so it'd keep showing the old contents of this filename);
    # END jumps to the last gallery slot, which is this custom file.
    press_key(e.KEY_R)
    press_key(e.KEY_END)


def reset_custom_slot():
    # Session-scoped reset: restore the neutral placeholder over whatever
    # the previous user uploaded, then send feh home. Called via img_copy
    # from CT300's on_start/on_dispose - this is what keeps one user's
    # image from leaking into the next user's session.
    shutil.copyfile(NEUTRAL_PATH, CUSTOM_SLOT_PATH)
    press_key(e.KEY_R)
    press_key(e.KEY_HOME)


def control_board(command):
    cmd = command.decode('utf-8', errors='ignore').strip()

    if cmd == 'img_next':
        press_key(e.KEY_SPACE)
    elif cmd == 'img_last':
        press_key(e.KEY_LEFT)
    elif cmd == 'img_home':
        press_key(e.KEY_R)
        press_key(e.KEY_HOME)
    elif cmd == 'img_end_':
        press_key(e.KEY_END)
    elif cmd == 'img_copy':
        reset_custom_slot()
    elif cmd == 'switch00':
        switches[0].off()
    elif cmd == 'switch01':
        switches[0].on()
    elif cmd == 'switch10':
        switches[1].off()
    elif cmd == 'switch11':
        switches[1].on()
    elif cmd == 'switch20':
        switches[2].off()
    elif cmd == 'switch21':
        switches[2].on()


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(5)
print("Cyclone IV lab server listening on {}:{}".format(HOST, PORT), flush=True)

while True:
    conn, addr = server.accept()
    try:
        data = conn.recv(8)
        if data:
            if data.decode('utf-8', errors='ignore').strip() == 'img_upld':
                # This command alone keeps reading more bytes off the same
                # connection (length prefix + image) - control_board() only
                # handles fixed 8-byte commands, so it's dispatched here.
                receive_image(conn)
            else:
                control_board(data)
            conn.sendall(data)
    except Exception as ex:
        print("Error: {}".format(ex), flush=True)
    finally:
        conn.close()
