"""Preview and writing must reuse identical bytes and invalidate changed input."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
from PIL import Image
import convert_media


class ConversionCacheTests(unittest.TestCase):
    def test_reuses_preview_bytes_and_invalidates_changed_source_or_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'image.png'
            output = Path(folder) / 'output.lg700boot'
            Image.new('RGB', (640, 480), '#1f739a').save(source)
            with patch('convert_media.build_asset', wraps=convert_media.build_asset) as encode:
                preview = convert_media.cached_build_asset(source, 75, allow_oversize=True)
                actual = convert_media.convert(source, output, 75)
                self.assertIs(actual, preview)
                self.assertEqual(output.read_bytes(), preview)
                self.assertEqual(encode.call_count, 1)
                stamp = source.stat().st_mtime_ns
                Image.new('RGB', (640, 480), '#ab3050').save(source)
                os.utime(source, ns=(stamp + 1000000, stamp + 1000000))
                changed = convert_media.cached_build_asset(source, 75)
                self.assertNotEqual(changed, preview)
                self.assertEqual(encode.call_count, 2)
                convert_media.cached_build_asset(source, 80)
                self.assertEqual(encode.call_count, 3)


if __name__ == '__main__':
    unittest.main()
