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
            imported = create('800-import-package')
            m = json.loads((imported / 'manifest.json').read_text(encoding='utf8'))
            m['kind'] = 'factory_asset_import'
            (imported / 'manifest.json').write_text(json.dumps(m), encoding='utf8')
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

    def test_restore_pauses_once_then_writes_and_verifies_without_restarting_loop(self):
        d=self.device();order=[]
        d.__enter__=Mock(return_value=d);d.__exit__=Mock(return_value=False)
        data=sample_boot();frame=unpack_boot(data)[0]
        d.set_offline_loop.side_effect=lambda enabled,*args:order.append(('mode',enabled))
        d.backup.side_effect=lambda *args:order.append('backup')
        d.write_asset.side_effect=lambda selector,*args,**kwargs:order.append(('write',selector))
        d.read_asset.side_effect=lambda selector:order.append(('read',selector)) or (frame,{})
        app=Mock(device_identity=None)
        app.target_profile.return_value=d.profile;app.vendor_session.side_effect=lambda:nullcontext()
        app.run.side_effect=lambda job:job()
        with patch('lg700_tool.LG700',return_value=d), \
                patch('lg700_tool.time.sleep'), \
                patch('lg700_tool.original_directory',return_value=Path('original')), \
                patch('lg700_tool.restore_manifest',return_value={}), \
                patch('lg700_tool.new_snapshot',return_value=Path('fresh')), \
                patch('lg700_tool.restore_file',side_effect=lambda path,selector,**kw:data if selector else frame):
            App.restore(app)
        self.assertEqual(order,[('mode',False),'backup',('write',1),('write',0),('read',0)])

    def test_missing_original_opens_import_picker_and_requires_fresh_backup(self):
        for failure in (None, 'backup_failed', 'wrong_device'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                d = self.device()
                d.__enter__ = Mock(return_value=d)
                d.__exit__ = Mock(return_value=False)
                order = []
                boot = sample_boot()
                standby = unpack_boot(boot)[0]
                device = {'model': 'lg700', 'vid': '1A2C', 'pid': '151C',
                          'size': [640, 480], 'revision': '0075',
                          'device_key': backup_device_key(d.info, d.profile)}
                assets = {name: {'file': f'{name}.bin', 'size': len(data), 'sha256': sha256(data)}
                          for name, data in [('boot', boot), ('standby', standby)]}
                source = root / 'import'
                source.mkdir()
                (source / 'boot.bin').write_bytes(boot)
                (source / 'standby.bin').write_bytes(standby)
                target = dict(device, device_key='different') if failure == 'wrong_device' else device
                (source / 'manifest.json').write_text(json.dumps({
                    'kind': 'factory_asset_import', 'device_role': 'import_target_not_capture',
                    'device': target, 'assets': assets,
                    'source_backup': {'device': dict(vid='1A2C', pid='151C', revision='0067')},
                }), encoding='utf8')
                fresh = root / 'fresh'
                cached = root / 'cached'
                d.set_offline_loop.side_effect = lambda enabled, path: order.append(('mode', enabled, path))

                def backup(path, progress):
                    order.append(('backup', path))
                    if failure == 'backup_failed':
                        raise OSError('fresh backup failed')

                d.backup.side_effect = backup
                d.write_asset.side_effect = lambda selector, data, path, *args, **kw: order.append(
                    ('write', selector, path, kw.get('recovery', False)))
                d.read_asset.side_effect = lambda selector: (order.append(('read', selector)) or standby, {})
                app = Mock(device_identity=None)
                app.target_profile.return_value = d.profile
                app.vendor_session.side_effect = lambda: nullcontext()
                app.run.side_effect = lambda job: job()
                with patch('lg700_tool.LG700', return_value=d), \
                        patch('lg700_tool.original_directory', side_effect=FileNotFoundError('missing')), \
                        patch('lg700_tool.filedialog.askdirectory', return_value=str(source)) as choose, \
                        patch('lg700_tool.messagebox.showerror') as error, \
                        patch('lg700_tool.cached_safety_backup', return_value=cached), \
                        patch('lg700_tool.new_snapshot', return_value=fresh), \
                        patch('lg700_tool.time.sleep'):
                    if failure == 'backup_failed':
                        with self.assertRaisesRegex(OSError, 'fresh backup failed'):
                            App.restore(app)
                    else:
                        App.restore(app)
                choose.assert_called_once()
                if failure == 'wrong_device':
                    app.run.assert_not_called()
                    error.assert_called_once()
                    self.assertEqual(order, [])
                elif failure == 'backup_failed':
                    self.assertEqual(order, [('mode', False, cached), ('backup', fresh)])
                else:
                    self.assertEqual(order, [('mode', False, cached), ('backup', fresh),
                                             ('write', 1, fresh, False), ('write', 0, fresh, False),
                                             ('read', 0)])

    def test_confirmed_original_conversion_reuses_fresh_backup_for_restore(self):
        cases = (
            ('cancel', None, []),
            ('success', None, ['backup', 'mode0', 'write1', 'write0']),
            ('wrong_device', '备份不属于当前设备', []),
            ('wrong_model', '备份型号与当前设备不匹配', []),
            ('unsupported', '目前仅支持同一台 LG700', []),
            ('corrupt', 'SHA-256 校验失败', []),
            ('backup_failed', 'fresh backup failed', ['backup']),
            ('write_failed', 'write failed', ['backup', 'mode0', 'write1']),
        )
        for case, failure, expected_order in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                old = Path(folder) / 'Original'
                old.mkdir()
                fresh = old.with_name('fresh')
                d = self.device()
                d.__enter__ = Mock(return_value=d)
                d.__exit__ = Mock(return_value=False)
                boot = sample_boot()
                standby = unpack_boot(boot)[0]
                source = dict(vid='1A2C', pid='151C', revision='0067')
                if case == 'wrong_device':
                    source.update(device_key='unrelated', revision='0075')
                elif case == 'wrong_model':
                    source.update(pid='1520')
                elif case == 'unsupported':
                    source.update(revision='0066')
                records = {}
                for name, data in (('boot', boot), ('standby', standby)):
                    filename = f'{name}.bin'
                    (old / filename).write_bytes(b'invalid' if case == 'corrupt' else data)
                    records[name] = dict(file=filename, sha256=sha256(data))
                manifest = old / 'manifest.json'
                original_manifest = json.dumps(dict(device=source, assets=records))
                manifest.write_text(original_manifest, encoding='utf8')
                order = []

                def backup(path, progress):
                    self.assertEqual(path, fresh)
                    order.append('backup')
                    if case == 'backup_failed':
                        raise OSError('fresh backup failed')

                def write(selector, data, path, progress, *, recovery=False):
                    self.assertEqual(path, fresh)
                    self.assertFalse(recovery)
                    self.assertEqual(data, boot if selector == 1 else standby)
                    order.append(f'write{selector}')
                    if case == 'write_failed':
                        raise OSError('write failed')

                d.backup.side_effect = backup
                d.write_asset.side_effect = write
                d.set_offline_loop.side_effect = lambda enabled, path: order.append(f'mode{int(enabled)}')
                d.read_asset.return_value = (standby, {})
                app = Mock(device_identity=None)
                app.target_profile.return_value = d.profile
                app.vendor_session.side_effect = nullcontext
                app.run.side_effect = lambda job: job()

                with patch('lg700_tool.LG700', return_value=d), \
                        patch('backup_store.device_home', return_value=old.parent), \
                        patch('lg700_tool.confirm_backup_conversion', return_value=case != 'cancel') as confirm, \
                        patch('lg700_tool.filedialog.askdirectory') as choose, \
                        patch('lg700_tool.cached_safety_backup', return_value=None), \
                        patch('lg700_tool.new_snapshot', return_value=fresh), \
                        patch('lg700_tool.time.sleep'):
                    if failure:
                        with self.assertRaisesRegex((OSError, ValueError), failure):
                            App.restore(app)
                    else:
                        App.restore(app)

                confirm.assert_called_once()
                choose.assert_not_called()
                self.assertEqual(order, expected_order)
                self.assertEqual(manifest.read_text(encoding='utf8'), original_manifest)
                if case == 'cancel':
                    app.run.assert_not_called()


if __name__=='__main__':unittest.main()
