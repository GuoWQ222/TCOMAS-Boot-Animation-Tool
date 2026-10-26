"""CLI diagnostics must handle busy devices without opening a fatal dialog."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import lg700_tool
from device_profiles import LX800_PROFILE


class DiagnosticCLITests(unittest.TestCase):
    def test_busy_device_returns_json_error_and_nonzero_status(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'diagnostic.json'
            with patch.object(lg700_tool.sys, 'argv', ['tool', '--inspect', '--model', 'lx800', '--output', str(output)]), \
                 patch.object(lg700_tool, 'LG700', side_effect=RuntimeError('another writer is active')), \
                 patch.object(lg700_tool.tk, 'Tk') as window:
                self.assertEqual(lg700_tool.main(), 2)
            self.assertEqual(json.loads(output.read_text(encoding='utf8')),
                {'error': 'another writer is active', 'model_requested': 'lx800', 'read_only': True})
            window.assert_not_called()

    def test_probe_reports_the_selected_model_and_visible_shape(self):
        device = Mock(profile=LX800_PROFILE, revision=0x75)
        device.__enter__ = Mock(return_value=device)
        device.__exit__ = Mock(return_value=False)
        device.metadata.side_effect = [{'selector': 1, 'size': 100}, {'selector': 0, 'size': 50}]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'diagnostic.json'
            with patch.object(lg700_tool.sys, 'argv', ['tool', '--inspect', '--model', 'lx800', '--output', str(output)]), \
                 patch.object(lg700_tool, 'LG700', return_value=device) as target:
                self.assertEqual(lg700_tool.main(), 0)
            result = json.loads(output.read_text(encoding='utf8'))
            self.assertEqual((result['model'], result['pid'], result['size'], result['visible_shape']),
                             ('TCOMAS LX800', '151D', [480, 480], 'circle'))
            target.assert_called_once_with(profile=LX800_PROFILE)
            device.write_asset.assert_not_called()
            device.set_offline_loop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
