#!/usr/bin/env python3
"""Encode screenshot frames; requires Pillow. No generated or retouched UI pixels."""
import json
from pathlib import Path
import sys

from PIL import Image

frames = json.loads(Path(sys.argv[1]).read_text())
images = [Image.open(frame["filename"]).convert("RGB") for frame in frames]
# One shared palette reduces flicker between the captured interface states.
palette_source = Image.new("RGB", (images[0].width, sum(image.height for image in images)))
y = 0
for image in images:
    palette_source.paste(image, (0, y))
    y += image.height
palette = palette_source.quantize(colors=192)
encoded = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
encoded[0].save(sys.argv[2], save_all=True, append_images=encoded[1:],
                duration=[frame["duration"] for frame in frames], loop=0, optimize=True)
