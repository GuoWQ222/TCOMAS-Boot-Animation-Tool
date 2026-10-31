"""Protect backup-first writes and reversible playback before a long read."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from device_profiles import LG700_PROFILE, backup_device_key
import maintenance_backup as mb
from lg700_tool import App
from sample_assets import sample_boot
from lg700_core import sha256, unpack_boot

META = dict(selector=1,size=32,active_record=0x400000,next_record=0x410000,extra=0)
EMPTY = dict(selector=1,size=0,active_record=0xFFFFFFFF,next_record=0x400000,extra=0)


class MaintenanceBackupTests(unittest.TestCase):
    def device(self):
        d = Mock(profile=LG700_PROFILE,revision=0x75)
        d.info = dict(product_string='LG700',serial_number='synthetic-maintenance',path=b'offline')
        d.metadata.return_value = META.copy()
        return d

    def test_cache_rejects_wrong_identity_revision_incomplete_and_corrupt_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            d=self.device()
            def create(name,**changes):
                p=root/name;p.mkdir()
                boot=sample_boot()
                standby=unpack_boot(boot)[0]
                (p/'boot.bin').write_bytes(boot);(p/'standby.bin').write_bytes(standby)
                device={'model':'lg700','revision':'0075','device_key':backup_device_key(d.info,d.profile)}
                device.update(changes)
                m={'device':device,'assets':{
                    'boot':{'file':'boot.bin','sha256':sha256(boot)},
                    'standby':{'file':'standby.bin','sha256':sha256(standby)}}}
                (p/'manifest.json').write_text(json.dumps(m),encoding='utf8')
                return p
            good=create('100-good')
            create('200-wrong-device',device_key='unrelated')
            create('300-wrong-revision',revision='0067')
            corrupt=create('400-corrupt');(corrupt/'boot.bin').write_bytes(b'bad')
            create('500-unbound',device_key='')
            incomplete=create('600-incomplete');(incomplete/'standby.bin').unlink()
            create('700-good.pending')
            with patch.object(mb,'device_home',return_value=root):
                self.assertEqual(mb.cached_safety_backup(d),good)
            d.set_offline_loop.assert_not_called()

    def test_pause_precedes_fresh_backup_and_never_replaces_it(self):
        d=self.device();order=[]
        d.set_offline_loop.side_effect=lambda enabled,path:order.append(('mode',enabled,path))
        d.backup.side_effect=lambda *args:order.append('backup') or {'complete':True}
        with patch.object(mb,'cached_safety_backup',return_value=Path('safety')):
            manifest,paused=mb.backup_before_update(d,Path('fresh'))
        self.assertEqual(order,[('mode',False,Path('safety')),'backup'])
        self.assertTrue(paused);self.assertTrue(manifest['complete'])
        d.backup.assert_called_once()
        self.assertEqual(d.backup.call_args.args[0],Path('fresh'))
        d.write_asset.assert_not_called()

    def test_no_cache_retains_original_read_only_backup(self):
        d=self.device()
        with patch.object(mb,'cached_safety_backup',return_value=None):
            _,paused=mb.backup_before_update(d,Path('fresh'))
        self.assertFalse(paused)
        d.metadata.assert_not_called();d.set_offline_loop.assert_not_called()
        d.backup.assert_called_once()

    def test_backup_failure_restores_loop_once_before_any_asset_write(self):
        d=self.device();d.backup.side_effect=OSError('failed read')
        with patch.object(mb,'cached_safety_backup',return_value=Path('safety')):
            with self.assertRaisesRegex(OSError,'failed read'):
                mb.backup_before_update(d,Path('fresh'))
        self.assertEqual([c.args[0] for c in d.set_offline_loop.call_args_list],[False,True])
        d.write_asset.assert_not_called()

    def test_empty_boot_failure_does_not_start_nonexistent_animation(self):
        d=self.device();d.metadata.return_value=EMPTY.copy()
        d.backup.side_effect=OSError('failed read')
        with patch.object(mb,'cached_safety_backup',return_value=Path('safety')):
            with self.assertRaisesRegex(OSError,'failed read'):
                mb.backup_before_update(d,Path('fresh'))
        d.set_offline_loop.assert_called_once_with(False,Path('safety'))
        d.write_asset.assert_not_called()

    def test_uncertain_pause_is_not_replayed_or_followed_by_backup(self):
        d=self.device();d.set_offline_loop.side_effect=RuntimeError('unknown send')
        with patch.object(mb,'cached_safety_backup',return_value=Path('safety')):
            with self.assertRaisesRegex(RuntimeError,'unknown send'):
                mb.backup_before_update(d,Path('fresh'))
        d.set_offline_loop.assert_called_once()
        d.backup.assert_not_called();d.write_asset.assert_not_called()

    def test_resume_failure_preserves_both_errors_and_never_retries(self):
        d=self.device();d.backup.side_effect=OSError('failed read')
        d.set_offline_loop.side_effect=[{},OSError('resume failed')]
        with patch.object(mb,'cached_safety_backup',return_value=Path('safety')):
            with self.assertRaisesRegex(RuntimeError,'failed read.*resume failed'):
                mb.backup_before_update(d,Path('fresh'))
        self.assertEqual(d.set_offline_loop.call_count,2)
        d.write_asset.assert_not_called()

    def test_write_ui_keeps_single_pause_and_pauses_before_backup(self):
        d=self.device();order=[]
        d.__enter__=Mock(return_value=d);d.__exit__=Mock(return_value=False)
        d.set_offline_loop.side_effect=lambda enabled,*args:order.append(('mode',enabled))
        d.backup.side_effect=lambda *args:order.append('backup') or {'assets':{'boot':{}}}
        d.last_write_skipped=False;d.last_verification_reconnected=False
        d.write_asset.side_effect=lambda selector,*args:order.append(('write',selector)) or 'sha256'
        data=sample_boot();d.read_asset.return_value=(unpack_boot(data)[0],{})
        app=Mock(device_identity=None)
        app.target_profile.return_value=d.profile;app.vendor_session.return_value=nullcontext()
        app.enable_offline.side_effect=lambda device,path:device.set_offline_loop(True,path)
        with patch('lg700_tool.LG700',return_value=d), \
                patch('lg700_tool.time.sleep'), \
                patch('lg700_tool.new_snapshot',return_value=Path('fresh')), \
                patch('lg700_tool.preserve_original',return_value=Path('original')), \
                patch.object(mb,'cached_safety_backup',return_value=Path('safety')):
            App.write_data(app,data)
        self.assertEqual(order,[('mode',False),'backup',('write',1),('write',0),('mode',True)])

    def test_original_preservation_failure_restores_loop_before_any_write(self):
        d=self.device()
        d.__enter__=Mock(return_value=d);d.__exit__=Mock(return_value=False)
        app=Mock(device_identity=None)
        app.target_profile.return_value=d.profile;app.vendor_session.return_value=nullcontext()
        with patch('lg700_tool.LG700',return_value=d), \
                patch('lg700_tool.new_snapshot',return_value=Path('fresh')), \
                patch('lg700_tool.preserve_original',side_effect=OSError('original invalid')), \
                patch('lg700_tool.backup_before_update',return_value=({'assets':{'boot':{}}},True)):
            with self.assertRaisesRegex(OSError,'original invalid'):
                App.write_data(app,sample_boot())
        d.set_offline_loop.assert_called_once_with(True,Path('fresh'))
        d.write_asset.assert_not_called()

    def test_restore_pauses_once_before_backup_then_writes_and_resumes(self):
        d=self.device();order=[]
        d.__enter__=Mock(return_value=d);d.__exit__=Mock(return_value=False)
        data=sample_boot();frame=unpack_boot(data)[0]
        d.set_offline_loop.side_effect=lambda enabled,*args:order.append(('mode',enabled))
        d.backup.side_effect=lambda *args:order.append('backup')
        d.write_asset.side_effect=lambda selector,*args,**kwargs:order.append(('write',selector))
        d.read_asset.return_value=(frame,{})
        app=Mock(device_identity=None)
        app.target_profile.return_value=d.profile;app.vendor_session.side_effect=lambda:nullcontext()
        app.run.side_effect=lambda job:job()
        with patch('lg700_tool.LG700',return_value=d), \
                patch('lg700_tool.time.sleep'), \
                patch('lg700_tool.original_directory',return_value=Path('original')), \
                patch('lg700_tool.new_snapshot',return_value=Path('fresh')), \
                patch('lg700_tool.restore_file',side_effect=lambda path,selector,**kw:data if selector else frame):
            App.restore(app)
        self.assertEqual(order,[('mode',False),'backup',('write',1),('write',0),('mode',True)])


if __name__=='__main__':unittest.main()
