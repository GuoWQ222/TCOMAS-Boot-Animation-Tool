"""Regression coverage for stale read responses and a failed pre-write backup."""
from contextlib import contextmanager
import struct
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from sample_assets import sample_boot
from lg700_core import LG700, ReadResponseError, unpack_boot
from lg700_tool import App
from device_profiles import LG700_PROFILE


META = {'selector': 1, 'size': 32, 'active_record': 0x400000,
        'next_record': 0x410000, 'extra': 0}


class ReadRecoveryTests(unittest.TestCase):
    def test_same_command_stale_offset_is_rejected_after_packet_collection(self):
        device = object.__new__(LG700)
        device.hid = Mock()
        device.hid.write.return_value = 441
        response = struct.pack('<IIIi', 0x8000010D, 128, 32, 0) + b'x' * 32
        device.hid.read.return_value = response.ljust(440, b'\0')
        with self.assertRaisesRegex(ReadResponseError, '返回 offset=128'):
            device.query(0x0D, struct.pack('<IIi', 0, 32, 0))
        device.hid.write.assert_called_once()

    def test_read_retries_once_without_appending_stale_bytes(self):
        device = object.__new__(LG700)
        device.metadata = Mock(return_value=META.copy())
        device._recover_read_session = Mock()
        device.query = Mock(side_effect=[ReadResponseError('old response'),
            struct.pack('<IIi', 0, 32, 0) + b'a' * 32])
        data, _ = device.read_asset(1)
        self.assertEqual(data, b'a' * 32)
        device._recover_read_session.assert_called_once_with(META)
        self.assertEqual(device.query.call_count, 2)
        self.assertTrue(all(call.args[0] == 0x0D for call in device.query.call_args_list))

    def test_persistent_failure_is_bounded_and_content_change_stops_read(self):
        device = object.__new__(LG700)
        device.metadata = Mock(return_value=META.copy())
        device._recover_read_session = Mock()
        device.query = Mock(side_effect=ReadResponseError('wrong length'))
        with self.assertRaisesRegex(OSError, '已停止本次操作'):
            device.read_asset(1)
        self.assertEqual(device.query.call_count, 2)
        device._recover_read_session.assert_called_once()
        device.query = Mock(return_value=struct.pack('<IIi', 0, 32, 0) + b'a' * 32)
        device.metadata = Mock(side_effect=[META.copy(), dict(META, next_record=0x420000)])
        with self.assertRaisesRegex(OSError, '元数据已改变'):
            device.read_asset(1)

    def test_backup_failure_cannot_send_a_write_and_vendor_is_resumed(self):
        order = []
        @contextmanager
        def paused():
            order.append('pause')
            try:
                yield
            finally:
                order.append('resume')
        fake_device = Mock()
        fake_device.__enter__ = Mock(return_value=fake_device)
        fake_device.__exit__ = Mock(return_value=False)
        def fail_backup(*args):
            order.append('backup')
            raise OSError('read failed before writing')
        fake_device.backup.side_effect = fail_backup
        app = Mock()
        app.target_profile.return_value = LG700_PROFILE
        app.device_identity = None
        app.vendor_session.side_effect = paused
        data = sample_boot()
        with patch('lg700_tool.pause_vendor', paused), \
                patch('lg700_tool.LG700', return_value=fake_device), \
                patch('lg700_tool.new_snapshot', return_value=Path('unused')):
            with self.assertRaisesRegex(OSError, 'before writing'):
                App.write_data(app, data)
        self.assertEqual(order, ['pause', 'backup', 'resume'])
        fake_device.write_asset.assert_not_called()
        fake_device.set_offline_loop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
