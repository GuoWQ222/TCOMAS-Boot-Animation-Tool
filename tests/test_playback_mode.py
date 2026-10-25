"""Offline tests for the native display-mode command and backup requirement."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lg700_core import LG700, sha256


class PlaybackModeTests(unittest.TestCase):
    def test_mode_command_requires_valid_backups_and_records_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            assets = {}
            for name in ('boot', 'standby'):
                data = (name + ' saved').encode()
                (directory / (name + '.bin')).write_bytes(data)
                assets[name] = {'file': name + '.bin', 'sha256': sha256(data)}
            (directory / 'manifest.json').write_text(json.dumps(
                {'device': {'revision': '0067'}, 'assets': assets}))
            device = object.__new__(LG700)
            device.revision = 0x67
            commands = []
            device.query = lambda op, payload: commands.append((op, payload))
            with patch('lg700_core.time.sleep'):
                result = device.set_offline_loop(True, directory)
            self.assertEqual(commands, [(0x1E, b'\x01')])
            self.assertTrue(result['acknowledged'])
            self.assertFalse(result['visual_playback_verified'])
            self.assertFalse(result['mode_readback_supported'])
            self.assertIsNone(result['previous_mode'])
            with patch.object(device, 'query', side_effect=OSError('read error')) as query, \
                    patch.object(device, '_reconnect_command_interface') as reconnect, \
                    patch('lg700_core.time.sleep'):
                result = device.set_offline_loop(True, directory)
            query.assert_called_once_with(0x1E, b'\x01')
            reconnect.assert_called_once_with()
            self.assertFalse(result['acknowledged'])
            self.assertTrue(result['device_reconnected'])
            self.assertFalse(result['visual_playback_verified'])
            (directory / 'boot.bin').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, '备份 SHA-256'):
                device.set_offline_loop(True, directory)
            self.assertEqual(len(commands), 1)

    def test_rejects_unrelated_device_configuration(self):
        device = object.__new__(LG700)
        for payload in (b'', b'\x02', b'\x01\x00', b'\xff'):
            with self.assertRaisesRegex(ValueError, '只允许'):
                device.query(0x1E, payload)
        with self.assertRaises(ValueError):
            device.query(0xFF, b'\x01')


if __name__ == '__main__':
    unittest.main()
