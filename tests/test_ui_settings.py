"""The first-use reminder survives a new process and tolerates bad preferences."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ui_settings import needs_startup_notice, remember_notice_read, preferences_path


class UISettingsTests(unittest.TestCase):
    def test_acknowledgement_is_saved_and_read_back_from_disk(self):
        with tempfile.TemporaryDirectory() as temp, patch('ui_settings.data_home', return_value=Path(temp)):
            self.assertTrue(needs_startup_notice())
            remember_notice_read()
            self.assertFalse(needs_startup_notice())
            self.assertEqual(json.loads(preferences_path().read_text())['notice_read'], True)

    def test_damaged_or_unexpected_preferences_show_notice(self):
        with tempfile.TemporaryDirectory() as temp, patch('ui_settings.data_home', return_value=Path(temp)):
            for data in ('broken json', 'null', '[]', '{"notice_read": "true"}'):
                preferences_path().write_text(data)
                self.assertTrue(needs_startup_notice())

    def test_unwritable_preference_does_not_block_main_application(self):
        with patch('ui_settings.preferences_path', return_value=Path('unused')), \
             patch.object(Path, 'write_text', side_effect=PermissionError('test read-only profile')), \
             self.assertLogs('lg700.ui', level='WARNING'):
            remember_notice_read()


if __name__ == '__main__':
    unittest.main()
