"""Offline protocol tests using synthetic media; never opens USB."""
import struct
import unittest
import json
import tempfile
from unittest.mock import Mock
from pathlib import Path
from sample_assets import sample_boot
from lg700_core import LG700, pack_boot, unpack_boot, validate_asset, encode_reports, capacity, sha256, READ_CHUNK, WRITE_CHUNK


class NativeFormatTests(unittest.TestCase):
    def test_container_fixture_roundtrip(self):
        data = sample_boot()
        frames = unpack_boot(data)
        self.assertEqual(len(frames), 3)
        self.assertEqual(pack_boot(frames), data)

    def test_capacity_checked_before_write(self):
        for selector in (0, 1):
            with self.assertRaisesRegex(ValueError, '容量'):
                validate_asset(selector, b'x' * (capacity(selector) + 1))

    def test_corrupt_container_header_rejected(self):
        data = bytearray(sample_boot())
        data[0] ^= 1
        with self.assertRaisesRegex(ValueError, '校验'):
            unpack_boot(data)

    def test_command_report_boundaries(self):
        for size in (0, 1, 436, 437, 8192, 21800):
            payload = bytes([37]) * size
            reports = list(encode_reports(11, payload))
            self.assertTrue(all(len(r) == 441 for r in reports))
            self.assertEqual(reports[0][1:5], struct.pack('<I', 0x8000000B | len(reports) << 8))
            self.assertEqual(b''.join(r[5:] for r in reports)[:size], payload)
        with self.assertRaises(ValueError):
            list(encode_reports(11, b'x' * 21801))
        self.assertEqual(READ_CHUNK, 8192)
        self.assertEqual(WRITE_CHUNK, 8192)
        self.assertEqual(len(list(encode_reports(11, b'\0' * (12 + WRITE_CHUNK)))), 19)

    def test_identical_asset_is_fully_verified_without_writing(self):
        data = sample_boot()
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'boot.bin').write_bytes(data)
            (directory / 'manifest.json').write_text(json.dumps({
                'device': {'revision': '0067'}, 'assets': {'boot': {
                    'file': 'boot.bin', 'size': len(data), 'sha256': sha256(data)}}}))
            device = object.__new__(LG700)
            device.revision = 0x67
            device.metadata = Mock(return_value={'size': len(data)})
            device.read_asset = Mock(return_value=(data, {}))
            device.query = Mock(side_effect=AssertionError('No write may be sent'))
            digest = device.write_asset(1, data, directory)
            self.assertEqual(digest, sha256(data))
            self.assertTrue(device.last_write_skipped)
            device.read_asset.assert_called_once()
            device.query.assert_not_called()
            device.read_asset = Mock(side_effect=[OSError('USB reset'), (data, {})])
            device._reconnect_command_interface = Mock()
            self.assertEqual(device.write_asset(1, data, directory), digest)
            self.assertTrue(device.last_verification_reconnected)
            self.assertEqual(device.read_asset.call_count, 2)
            device._reconnect_command_interface.assert_called_once()
            device.read_asset = Mock(return_value=(b'changed after backup', {}))
            with self.assertRaisesRegex(ValueError, '备份后素材内容发生变化'):
                device.write_asset(1, data, directory)
            device.query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
