"""Cropping preserves geometry, compatibility and preview/write byte identity."""
import io
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
import convert_media
from lg700_core import unpack_boot
from quality_preview import QualityPreview
from device_profiles import LG700_PROFILE


def first_frame(data):
    return Image.open(io.BytesIO(unpack_boot(data)[0])).convert('RGB')


class FillScreenTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        convert_media._conversion_cache.clear()

    def source(self, size):
        path = Path(self.folder.name) / 'source.png'
        image = Image.new('RGB', size, '#29a37a')
        draw = ImageDraw.Draw(image)
        x, y = size[0] // 2, size[1] // 2
        draw.ellipse((x - 80, y - 80, x + 80, y + 80), fill='#ec3e4a')
        image.save(path)
        return path

    def assert_circle(self, frame, expected_diameter):
        # Locate the red circle after JPEG encoding.
        points = [(x, y) for y in range(frame.height) for x in range(frame.width)
                  if (lambda rgb: rgb[0] > 160 and rgb[1] < 120)(frame.getpixel((x, y)))]
        width = max(p[0] for p in points) - min(p[0] for p in points) + 1
        height = max(p[1] for p in points) - min(p[1] for p in points) + 1
        self.assertLessEqual(abs(width - height), 2)
        self.assertLessEqual(abs(width - expected_diameter), 3)

    def test_wide_and_tall_crop_fill_without_stretching(self):
        for size, diameter in (((1200, 480), 160), ((640, 1200), 160)):
            with self.subTest(size=size):
                source = self.source(size)
                framed = first_frame(convert_media.build_asset(source))
                filled = first_frame(convert_media.build_asset(source, fill_screen=True))
                self.assertEqual(filled.size, (640, 480))
                self.assertLess(max(framed.getpixel((10, 10))), 5)
                green = filled.getpixel((10, 10))
                self.assertGreater(green[1], green[0] + 60)
                self.assert_circle(filled, diameter)

    def test_unchecked_matches_legacy_image_encoding_exactly(self):
        from PIL import ImageOps
        source = self.source((1200, 480))
        with Image.open(source) as image:
            fitted = ImageOps.contain(image.convert('RGB'), (640, 480))
            canvas = Image.new('RGB', (640, 480))
            canvas.paste(fitted, ((640 - fitted.width) // 2, (480 - fitted.height) // 2))
            encoded = io.BytesIO()
            canvas.save(encoded, 'JPEG', quality=75, progressive=False, subsampling=2)
        self.assertEqual(unpack_boot(convert_media.build_asset(source))[0], encoded.getvalue())
        self.assertEqual(convert_media.build_asset(source),
                         convert_media.build_asset(source, fill_screen=False))

    def test_cache_separates_modes_and_write_reuses_selected_preview(self):
        source = self.source((1200, 480))
        output = Path(self.folder.name) / 'filled.lg700boot'
        with patch('convert_media.build_asset', wraps=convert_media.build_asset) as encode:
            framed = convert_media.cached_build_asset(source, allow_oversize=True)
            filled = convert_media.cached_build_asset(source, allow_oversize=True, fill_screen=True)
            self.assertNotEqual(framed, filled)
            self.assertIs(convert_media.convert(source, output, fill_screen=True), filled)
            self.assertEqual(output.read_bytes(), filled)
            self.assertIs(convert_media.cached_build_asset(source, fill_screen=False), framed)
            self.assertEqual(encode.call_count, 2)

    def test_quality_worker_preserves_mode_in_result(self):
        events = queue.Queue()
        worker = QualityPreview(events)
        self.addCleanup(worker.close)
        source = self.source((1200, 480))
        worker.request(7, source, 75, True)
        result = events.get(timeout=10)
        self.assertEqual(result[:5], ('quality_result', 7, str(source), 75, True))
        self.assertIsNone(result[6])
        self.assertGreater(first_frame(result[5]).getpixel((10, 10))[1], 130)

    def test_gui_rejects_result_for_previous_mode(self):
        from lg700_tool import App
        app = Mock(busy=False, preview_generation=2, preview_source='video.mp4')
        app.selected_quality.return_value = 75
        app.fill_screen.get.return_value = True
        App.accept_quality_result(app, ('quality_result', 2, 'video.mp4', 75, False, b'old', None))
        app.apply_preview_data.assert_not_called()
        App.accept_quality_result(app, ('quality_result', 2, 'video.mp4', 75, True, b'new', None))
        app.apply_preview_data.assert_called_once_with(75, b'new')

    def test_custom_write_button_passes_the_selected_mode(self):
        import lg700_tool
        for mode in (False, True):
            for method in ('choose_media',):
                with self.subTest(mode=mode, method=method):
                    app = Mock()
                    app.target_profile.return_value = LG700_PROFILE
                    app.device_identity = None
                    app.selected_quality.return_value = 75
                    app.fill_screen.get.return_value = mode
                    app.run.side_effect = lambda job: job()
                    source = str(self.source((1200, 480)))
                    with patch.object(lg700_tool.filedialog, 'askopenfilename', return_value=source), \
                         patch.object(lg700_tool, 'detect_current_media', return_value={'path': source}), \
                         patch.object(lg700_tool, 'data_home', return_value=Path(self.folder.name)), \
                         patch.object(lg700_tool, 'convert', return_value=b'prepared') as conversion:
                        getattr(lg700_tool.App, method)(app)
                    self.assertEqual(conversion.call_args.kwargs,
                                     {'fill_screen': mode, 'profile': LG700_PROFILE})
                    app.write_data.assert_called_once_with(b'prepared', profile=LG700_PROFILE,
                                                          expected_identity=None)


if __name__ == '__main__':
    unittest.main()
