"""Synthetic test-only media. No device access or private factory/user files."""
from functools import lru_cache
import io
from PIL import Image
from lg700_core import pack_boot


@lru_cache(maxsize=1)
def sample_boot():
    frames = []
    for color in ('#225577', '#446633', '#774455'):
        image = Image.new('RGB', (640, 480), color)
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=75, progressive=False, subsampling=2)
        frames.append(output.getvalue())
    return pack_boot(frames)
