#!/usr/bin/env python3
# Generates custom_neutral.jpg - the "no custom image yet" placeholder
# copied over the gallery's custom slot on every session reset (see
# io_interface.py's reset_custom_slot()/img_copy). Re-run this any time
# the Pi is set up fresh; the file itself isn't checked into the repo
# since it's generated output, not source.
#
# Usage: python3 make_custom_placeholder.py
# Writes to /home/pi/custom_neutral.jpg (matches NEUTRAL_PATH in
# io_interface.py).

from PIL import Image, ImageDraw, ImageFont

OUTPUT_PATH = '/home/pi/custom_neutral.jpg'
SIZE = (1280, 720)  # matches the board's native HDMI/capture resolution


def centered(draw, text, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((SIZE[0] - w) // 2, y), text, font=font, fill=fill)


def main():
    img = Image.new('RGB', SIZE, color=(30, 34, 45))
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.load_default(size=60)
        font_small = ImageFont.load_default(size=34)
    except TypeError:
        # Older Pillow without the `size` kwarg on load_default()
        font_big = font_small = ImageFont.load_default()

    centered(draw, 'No custom image yet', 290, font_big, (235, 238, 245))
    centered(draw, 'Use the Upload button to place your own image here',
             400, font_small, (150, 160, 180))
    img.save(OUTPUT_PATH, 'JPEG', quality=90)
    print('Wrote {}'.format(OUTPUT_PATH))


if __name__ == '__main__':
    main()
