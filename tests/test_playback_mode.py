"""Offline tests for the native display-mode command and backup requirement."""
import json
from pathlib import Path
import tempfile
import unittest
import struct
from unittest.mock import Mock, patch
from lg700_core import LG700, sha256


class PlaybackModeTests(unittest.TestCase):
    def test_mode_has_no_ack_and_requires_valid_backups(self):
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
            device.hid = Mock()
            device.hid.write.return_value = 441
            device.hid.read.return_value = struct.pack('<IH',0x80000187,0x67).ljust(440,b'\0')
            with patch('lg700_core.time.sleep'):
                result = device.set_offline_loop(True, directory)
            commands = [call.args[0][1] for call in device.hid.write.call_args_list]
            self.assertEqual(commands, [0x1E,0x87])
            self.assertEqual(device.hid.read.call_count,1)
            self.assertFalse(result['acknowledged'])
            self.assertFalse(result['ack_expected'])
            self.assertTrue(result['send_completed'])
            self.assertTrue(result['communication_verified'])
            self.assertFalse(result['visual_playback_verified'])
            self.assertFalse(result['mode_readback_supported'])
            self.assertIsNone(result['previous_mode'])
            with patch.object(device, 'query', side_effect=[b'',OSError('read error')]) as query, \
                    patch.object(device, '_reconnect_command_interface') as reconnect, \
                    patch('lg700_core.time.sleep'):
                result = device.set_offline_loop(True, directory)
            self.assertEqual(query.call_args_list[0].args,(0x1E,b'\x01'))
            self.assertEqual(query.call_count,2)
            reconnect.assert_called_once_with()
            self.assertFalse(result['acknowledged'])
            self.assertTrue(result['device_reconnected'])
            self.assertFalse(result['visual_playback_verified'])
            (directory / 'boot.bin').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, '备份 SHA-256'):
                device.set_offline_loop(True, directory)
            self.assertEqual(device.hid.write.call_count,2)

    def test_mode_output_failure_is_not_retried_or_treated_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            assets={}
            for name in ('boot','standby'):
                data=b'backup';(directory/(name+'.bin')).write_bytes(data)
                assets[name]={'file':name+'.bin','sha256':sha256(data)}
            (directory/'manifest.json').write_text(json.dumps({'device':{'revision':'0067'},'assets':assets}))
            device=object.__new__(LG700);device.revision=0x67
            device.hid=Mock();device.hid.write.side_effect=OSError('disconnected')
            with self.assertRaisesRegex(RuntimeError,'没有重复发送'):
                device.set_offline_loop(False,directory)
            device.hid.write.assert_called_once()
            device.hid.read.assert_not_called()
            audit=json.loads((directory/'playback_mode.json').read_text())
            self.assertFalse(audit['send_completed'])
            self.assertFalse(audit['communication_verified'])

    def test_rejects_unrelated_device_configuration(self):
        device = object.__new__(LG700)
        for payload in (b'', b'\x02', b'\x01\x00', b'\xff'):
            with self.assertRaisesRegex(ValueError, '只允许'):
                device.query(0x1E, payload)
        with self.assertRaises(ValueError):
            device.query(0xFF, b'\x01')


if __name__ == '__main__':
    unittest.main()
