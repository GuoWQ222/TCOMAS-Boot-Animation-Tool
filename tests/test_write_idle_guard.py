"""Protect native mode throughout writing and readback; never resume on failure."""
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from sample_assets import sample_boot
from lg700_tool import App


class WriteIdleGuardTests(unittest.TestCase):
    def run_write(self, fail_boot=False):
        order=[]
        device=Mock()
        device.__enter__=Mock(return_value=device)
        device.__exit__=Mock(return_value=False)
        device.last_verification_reconnected=False
        device.last_write_skipped=False
        device.backup.side_effect=lambda *args:order.append('backup')
        device.set_offline_loop.side_effect=lambda enabled,*args:order.append(('mode',enabled))
        def write(selector,*args):
            order.append(('write-and-verify',selector))
            if selector==1 and fail_boot:raise OSError('test interruption')
            return 'verified-sha256'
        device.write_asset.side_effect=write
        app=Mock()
        app.enable_offline.side_effect=lambda device,directory:device.set_offline_loop(True,directory)
        with patch('lg700_tool.pause_vendor',return_value=nullcontext()), \
             patch('lg700_tool.LG700',return_value=device), \
             patch('lg700_tool.new_snapshot',return_value=Path('unused-snapshot')), \
             patch('lg700_tool.preserve_original',return_value=Path('unused-original')):
            if fail_boot:
                with self.assertRaisesRegex(OSError,'test interruption'):
                    App.write_data(app,sample_boot())
            else:
                App.write_data(app,sample_boot())
        return order

    def test_pause_follows_backup_and_resume_follows_both_full_verifications(self):
        self.assertEqual(self.run_write(),['backup',('mode',False),
            ('write-and-verify',0),('write-and-verify',1),('mode',True)])

    def test_interrupted_write_keeps_loop_disabled_and_does_not_retry(self):
        self.assertEqual(self.run_write(True),['backup',('mode',False),
            ('write-and-verify',0),('write-and-verify',1)])


if __name__=='__main__':unittest.main()
