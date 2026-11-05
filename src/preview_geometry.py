"""Preview the visible panel while leaving the encoded JPEG unchanged."""
import io
from PIL import Image
from device_profiles import resolve_profile


def preview_frame(jpeg, profile, size):
    profile = resolve_profile(profile)
    with Image.open(io.BytesIO(jpeg)) as source:
        image = source.convert('RGB').resize(size, Image.Resampling.LANCZOS)
    return image
