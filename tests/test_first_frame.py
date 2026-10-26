"""Hardware first-frame previews never use vendor selections or full reads."""
from contextlib import nullcontext
import io
import queue
import struct
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from device_profiles import PROFILES, LG700_PROFILE
from first_frame import read_boot_first_frame
from lg700_core import LG700, READ_CHUNK, checksum_record, pack_boot, unpack_boot
from lg700_tool import App


def encoded_frame(profile=LG700_PROFILE, color='#347b92'):
    buffer = io.BytesIO()
    Image.new('RGB', profile.size, color).save(buffer, 'JPEG', quality=75)
    return buffer.getvalue()


class FakeDevice:
    def __init__(self, data, profile=LG700_PROFILE):
        self.data, self.profile, self.reads = data, profile, []
        self.meta = dict(selector=1, size=len(data), active_record=0x400000,
                         next_record=0xf00000, extra=0)
        self._recover_read_session = Mock()

    def metadata(self, selector):
        if selector != 1:
            raise AssertionError('Only the boot asset is queried')
        return self.meta.copy()

    def query(self, op, payload):
        if op != 0x0D:
            raise AssertionError('First-frame preview must not write or switch modes')
        offset, count, reserved = struct.unpack('<IIi', payload)
        assert 0 < count <= READ_CHUNK and reserved == 0
        self.reads.append((offset, count))
        return struct.pack('<IIi', offset, count, 0) + self.data[offset:offset + count]


class FirstFrameTests(unittest.TestCase):
    def test_native_dimensions_for_all_models_and_no_full_read(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key):
                jpeg = encoded_frame(profile)
                data = pack_boot([jpeg] * 300)
                device = FakeDevice(data, profile)
                progress = []
                snapshot = read_boot_first_frame(device, progress.append)
                self.assertEqual(snapshot['jpeg'], jpeg)
                self.assertEqual(snapshot['frame_count'], 300)
                self.assertEqual(snapshot['total_size'], len(data))
                self.assertLess(snapshot['read_bytes'], len(data) // 50)
                first_end = 17 + 300 * 16 + len(b'0.jpg\0') + len(jpeg)
                self.assertTrue(all(off + count <= first_end for off, count in device.reads))
                self.assertEqual(progress[-1], 1)

    def test_partial_reads_preserve_eight_kibibyte_limit(self):
        # A noisy JPEG exercises multiple reports/blocks without any device I/O.
        image = Image.effect_noise(LG700_PROFILE.size, 80).convert('RGB')
        buffer = io.BytesIO(); image.save(buffer, 'JPEG', quality=75)
        data = pack_boot([buffer.getvalue()] * 3)
        device = FakeDevice(data)
        read_boot_first_frame(device)
        self.assertTrue(any(count == READ_CHUNK for _, count in device.reads))
        self.assertTrue(all(count <= READ_CHUNK for _, count in device.reads))

    def test_bad_root_is_rejected_before_reading_image(self):
        data = bytearray(pack_boot([encoded_frame()] * 2)); data[0] ^= 1
        device = FakeDevice(bytes(data))
        with self.assertRaisesRegex(ValueError, '包头'):
            read_boot_first_frame(device)
        self.assertEqual(device.reads, [(0, 16)])

    def test_out_of_range_first_record_is_rejected_before_payload(self):
        data = bytearray(pack_boot([encoded_frame()] * 2))
        offset, length, name_length, flags = len(data) + 99, 100, 6, 0
        struct.pack_into('<IIIHH', data, 17, checksum_record(offset, length, name_length, flags),
                         offset, length, name_length, flags)
        device = FakeDevice(bytes(data))
        with self.assertRaisesRegex(ValueError, '越界'):
            read_boot_first_frame(device)
        self.assertEqual(device.reads, [(0, 16), (16, 17)])

    def test_valid_jpeg_of_wrong_model_is_not_shown(self):
        device = FakeDevice(pack_boot([encoded_frame(PROFILES[1])]))
        with self.assertRaisesRegex(ValueError, '640×480'):
            read_boot_first_frame(device)

    def test_torn_frame_snapshot_is_rejected(self):
        device = FakeDevice(pack_boot([encoded_frame()] * 2))
        query = device.query
        payload_reads = 0
        def torn(op, payload):
            nonlocal payload_reads
            result = query(op, payload)
            offset = struct.unpack_from('<I', payload)[0]
            if offset > 33:
                payload_reads += 1
                if payload_reads == 2:
                    result = result[:-1] + bytes([result[-1] ^ 1])
            return result
        device.query = torn
        with self.assertRaisesRegex(OSError, '不一致'):
            read_boot_first_frame(device)

    def test_changed_metadata_is_rejected(self):
        device = FakeDevice(pack_boot([encoded_frame()] * 2))
        device.metadata = Mock(side_effect=[device.meta.copy(), dict(device.meta, active_record=0x410000)])
        with self.assertRaisesRegex(OSError, '记录发生变化'):
            read_boot_first_frame(device)

    def test_bad_response_gets_only_one_read_only_retry(self):
        device = FakeDevice(pack_boot([encoded_frame()]))
        device.query = Mock(return_value=struct.pack('<IIi', 999, 16, 0) + b'\0' * 16)
        with self.assertRaisesRegex(OSError, '不匹配'):
            read_boot_first_frame(device)
        self.assertEqual(device.query.call_count, 2)
        device._recover_read_session.assert_called_once()

    def test_device_method_uses_the_bounded_reader(self):
        device = FakeDevice(pack_boot([encoded_frame()]))
        self.assertEqual(LG700.read_boot_first_frame(device)['frame_count'], 1)


class DetectionTests(unittest.TestCase):
    def app(self):
        app = Mock(busy=False, events=queue.Queue())
        app.model_choice.get.return_value = '自动识别'
        app.run.side_effect = lambda job: job()
        app.vendor_session.return_value = nullcontext()
        return app

    def device(self):
        device = Mock(profile=LG700_PROFILE, revision=0x67)
        device.info = dict(product_string='LG700', path=b'fake', vendor_id=0x1a2c, product_id=0x151c)
        device.__enter__ = Mock(return_value=device)
        device.__exit__ = Mock(return_value=False)
        device.metadata.return_value = dict(size=10_619_168)
        device.read_boot_first_frame.return_value = {'jpeg': b'test frame', 'frame_count': 324,
            'total_size': 10_619_168, 'frame_size': 50_000, 'read_bytes': 100_000, 'seconds': 1.5}
        return device

    def test_detection_reads_hardware_frame_and_does_not_parse_vendor_selection(self):
        import lg700_tool
        app, device = self.app(), self.device()
        with patch.object(lg700_tool, 'LG700', return_value=device), \
             patch('current_media.detect_current_media', side_effect=AssertionError('No vendor configuration')):
            App.detect(app)
        device.read_boot_first_frame.assert_called_once()
        app.vendor_session.assert_not_called()
        device.read_asset.assert_not_called()
        device.write_asset.assert_not_called()
        device.set_offline_loop.assert_not_called()
        events = list(app.events.queue)
        self.assertTrue(any(event[0] == 'hardware_first_frame' for event in events))
        self.assertFalse(any(event[0] == 'current' for event in events))

    def test_failed_hardware_frame_is_never_replaced_with_official_material(self):
        import lg700_tool
        app, device = self.app(), self.device()
        device.read_boot_first_frame.side_effect = OSError('torn read')
        with patch.object(lg700_tool, 'LG700', return_value=device), \
             patch('current_media.detect_current_media', side_effect=AssertionError('No fallback')):
            App.detect(app)
        self.assertIn(('media_missing',), list(app.events.queue))
        device.set_offline_loop.assert_not_called()
        device.read_asset.assert_not_called()

    def test_first_frame_preview_keeps_full_hardware_size_without_reencoding(self):
        app = self.app()
        snapshot = self.device().read_boot_first_frame.return_value
        App.apply_hardware_first_frame(app, snapshot)
        self.assertEqual(app.frames, [snapshot['jpeg']])
        self.assertIsNone(app.preview_source)
        self.assertEqual(app.preview_kind, 'hardware_first_frame')
        self.assertIsNone(app.asset)
        app.quality_size.set.assert_called_once_with('硬件动画：10.13 MiB')
        app.request_quality_preview.assert_not_called()

    def test_quality_change_does_not_reencode_hardware_frame(self):
        app = Mock(preview_generation=0, preview_after=None, preview_source=None,
                   playing=False, preview_kind='hardware_first_frame')
        App.request_quality_preview(app)
        app.quality_worker.request.assert_not_called()
        app.quality_size.set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
