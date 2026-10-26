"""Preview the visible panel while leaving the encoded JPEG unchanged."""
import io
from PIL import Image, ImageDraw
from device_profiles import resolve_profile


def preview_frame(jpeg, profile, size):
    profile = resolve_profile(profile)
    with Image.open(io.BytesIO(jpeg)) as source:
        image = source.convert('RGB').resize(size, Image.Resampling.LANCZOS)
    if profile.visible_shape == 'circle':
        # A circular panel receives a square raster. Its corners are invisible.
        mask = Image.new('L', (size[0] * 3, size[1] * 3))
        ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        background = Image.new('RGB', size, '#151b24')
        background.paste(image, mask=mask)
        return background
    return image
