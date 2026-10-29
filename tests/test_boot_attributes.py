"""Native record attributes: synthetic media and read-only device substitutes."""
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
from device_profiles import PROFILES
from first_frame import read_boot_first_frame
from lg700_core import LG700, checksum_record, pack_boot, restore_file, unpack_boot


def encoded_frame(profile):
    output = io.BytesIO()
    Image.new('RGB', profile.size, '#345678').save(output, 'JPEG')
    return output.getvalue()


def attributed_boot(profile, root_tag=0x7000, frame_flags=0x6000):
    data = bytearray(pack_boot([encoded_frame(profile)] * 80))
    count = struct.unpack_from('<H', data, 14)[0] & 0xFFF
    struct.pack_into('<H', data, 14, root_tag | count)
    for index in range(count):
        position = 17 + index * 16
        _, off, length, name_len, _ = struct.unpack_from('<IIIHH', data, position)
        struct.pack_into('<IIIHH', data, position,
                         checksum_record(off, length, name_len, frame_flags),
                         off, length, name_len, frame_flags)
    return bytes(data)


class CapturedDevice:
    def __init__(self, data, profile):
        self.data, self.profile, self.reads = data, profile, []
        self.meta = dict(selector=1, size=len(data), active_record=0x400000,
                         next_record=0xf00000, extra=0)

    def metadata(self, selector):
        assert selector == 1
        return self.meta.copy()

    def query(self, op, payload):
        assert op == 0x0D, 'Only bounded reads may be issued'
        offset, length, reserved = struct.unpack('<IIi', payload)
        assert reserved == 0 and 0 < length <= 8192
        self.reads.append((offset, length))
        return struct.pack('<IIi', offset, length, 0) + self.data[offset:offset + length]


class BootAttributeTests(unittest.TestCase):
    def test_known_attributes_decode_identically_for_each_model(self):
        for profile in PROFILES:
            for root_tag, frame_flags in ((0x1000, 0), (0x7000, 0x6000)):
                with self.subTest(model=profile.key, root_tag=root_tag):
                    data = attributed_boot(profile, root_tag, frame_flags)
                    self.assertEqual(unpack_boot(data, profile=profile), [encoded_frame(profile)] * 80)

    def test_attributed_first_frame_is_bounded_and_matches_full_parser(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key):
                data = attributed_boot(profile)
                device = CapturedDevice(data, profile)
                snapshot = read_boot_first_frame(device)
                self.assertEqual(snapshot['jpeg'], unpack_boot(data, profile=profile)[0])
                self.assertEqual(snapshot['frame_count'], 80)
                first_end = 17 + 80 * 16 + len(b'0.jpg\0') + len(snapshot['jpeg'])
                self.assertTrue(all(off + length <= first_end for off, length in device.reads))
                self.assertLess(snapshot['read_bytes'], len(data) // 10)

    def test_backup_and_restore_keep_attributes_and_jpeg_bytes(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key), tempfile.TemporaryDirectory() as folder:
                data, standby = attributed_boot(profile), encoded_frame(profile)
                device = object.__new__(LG700)
                device._profile, device.revision = profile, 0x75
                device.info = dict(vendor_id=profile.vid, product_id=profile.pid,
                                   product_string=profile.name, serial_number='synthetic', path=b'offline')
                def read_asset(selector, progress):
                    content = data if selector else standby
                    return content, dict(selector=selector, size=len(content),
                                         active_record=profile.regions[selector][0],
                                         next_record=profile.regions[selector][1], extra=0)
                device.read_asset = Mock(side_effect=read_asset)
                device.query = Mock(side_effect=AssertionError('Backup/restore validation must not write'))
                directory = Path(folder) / 'snapshot'
                device.backup(directory)
                self.assertEqual((directory / 'boot.bin').read_bytes(), data)
                self.assertEqual(restore_file(directory, 1, profile=profile), data)
                self.assertEqual(restore_file(directory, 0, profile=profile), standby)
                device.query.assert_not_called()

    def test_unknown_root_tags_are_rejected_by_both_readers(self):
        profile = PROFILES[0]
        for tag in (0x0000, 0x3000, 0x5000, 0x9000, 0xF000):
            with self.subTest(tag=tag):
                data = attributed_boot(profile, root_tag=tag)
                with self.assertRaisesRegex(ValueError, '格式'):
                    unpack_boot(data, profile=profile)
                device = CapturedDevice(data, profile)
                with self.assertRaisesRegex(ValueError, '包头'):
                    read_boot_first_frame(device)
                self.assertEqual(device.reads, [(0, 16)])

    def test_unknown_frame_flags_are_rejected_before_jpeg_reads(self):
        profile = PROFILES[0]
        for flags in (0x1000, 0x2000, 0x4000, 0x8000, 0x6001):
            with self.subTest(flags=flags):
                data = attributed_boot(profile, frame_flags=flags)
                with self.assertRaisesRegex(ValueError, '标志'):
                    unpack_boot(data, profile=profile)
                device = CapturedDevice(data, profile)
                with self.assertRaisesRegex(ValueError, '记录'):
                    read_boot_first_frame(device)
                self.assertEqual(device.reads, [(0, 16), (16, 17)])

    def test_attributes_do_not_bypass_checksum_boundary_or_jpeg_validation(self):
        profile = PROFILES[0]
        baseline = attributed_boot(profile)
        variants = []
        header = bytearray(baseline); header[0] ^= 1
        variants.append(header)
        frame = bytearray(baseline); frame[17] ^= 1
        variants.append(frame)
        bounds = bytearray(baseline)
        _, _, length, name_len, flags = struct.unpack_from('<IIIHH', bounds, 17)
        off = len(bounds) + 1
        struct.pack_into('<IIIHH', bounds, 17, checksum_record(off, length, name_len, flags),
                         off, length, name_len, flags)
        variants.append(bounds)
        jpeg = bytearray(baseline)
        _, off, _, name_len, _ = struct.unpack_from('<IIIHH', jpeg, 17)
        jpeg[off + name_len] = 0
        variants.append(jpeg)
        for index, data in enumerate(variants):
            with self.subTest(corruption=index):
                with self.assertRaises(ValueError):
                    unpack_boot(bytes(data), profile=profile)
                with self.assertRaises(ValueError):
                    read_boot_first_frame(CapturedDevice(bytes(data), profile))

    def test_attributed_container_still_requires_terminal_record(self):
        profile = PROFILES[0]
        data = bytearray(attributed_boot(profile)); data[-1] ^= 1
        with self.assertRaisesRegex(ValueError, '结束记录'):
            unpack_boot(bytes(data), profile=profile)

    def test_newly_packed_assets_keep_the_existing_native_format(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key):
                data = pack_boot([encoded_frame(profile)])
                self.assertEqual(struct.unpack_from('<H', data, 14)[0], 0x1001)
                self.assertEqual(struct.unpack_from('<H', data, 31)[0], 0)
                self.assertEqual(data[-16:], struct.pack('<IIIHH', 0, 0, 0, 0, 0x1000))


if __name__ == '__main__':
    unittest.main()
