"""Faults found during the v1.2.11 audit; no physical device is opened."""
from contextlib import nullcontext
import io
import json
from pathlib import Path
import queue
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import lg700_core as core
import lg700_tool as gui
from device_profiles import LG700_PROFILE, PROFILES, BackupDeviceMismatch, check_manifest_device
from error_details import describe_error, concise_error
from sample_assets import sample_boot
from test_empty_boot import StoredDevice, frame


class ErrorDetailsTests(unittest.TestCase):
    def test_backup_warning_displays_manifest_revision_and_current_revision(self):
        for revision, expected in (('0067', 'REV 0067'), (None, '未记录')):
            with self.subTest(revision=revision):
                manifest = {'device': {'model': 'lg700', 'revision': revision}}
                with self.assertRaises(BackupDeviceMismatch) as raised:
                    check_manifest_device(manifest, LG700_PROFILE, revision=0x75)
                with patch.object(gui, 'confirm_warning', return_value=False) as warning:
                    gui.confirm_backup_conversion(Mock(), raised.exception, 'LG700 REV 0075')
                message = warning.call_args.kwargs['message']
                self.assertIn('备份固件：' + expected, message)
                self.assertIn('当前屏幕：LG700 REV 0075', message)

    def test_explicit_cause_includes_file_and_system_reason(self):
        inner = PermissionError(13, 'Access is denied', 'backup/boot.bin')
        outer = RuntimeError('完整备份当前素材失败')
        outer.__cause__ = inner
        text = describe_error(outer)
        for expected in ('完整备份', 'Access is denied', 'boot.bin', '底层原因'):
            self.assertIn(expected, text)

    def test_context_empty_message_suppression_and_cycles(self):
        outer = RuntimeError('USB operation failed')
        inner = TimeoutError()
        outer.__context__ = inner
        inner.__context__ = outer
        self.assertIn('TimeoutError', describe_error(outer))
        outer.__suppress_context__ = True
        self.assertEqual(describe_error(outer), 'USB operation failed')

    def test_browser_exception_is_visible_with_manual_link(self):
        with patch.object(gui, 'confirm_warning', return_value=True), \
             patch.object(gui.webbrowser, 'open', side_effect=OSError('browser permission denied')), \
             patch.object(gui.messagebox, 'showerror') as show:
            gui.show_firmware_upgrade(Mock(), 'LG700', 0x67)
        self.assertIn('访问权限不足', show.call_args.args[1])
        self.assertNotIn('browser permission denied', show.call_args.args[1])
        self.assertIn(gui.FIRMWARE_DOWNLOAD_URL, show.call_args.args[1])

    def test_worker_preserves_stage_cause_log_and_done_event(self):
        app = Mock(busy=False, preview_generation=0, preview_after=None,
                   buttons=[], events=queue.Queue(), log_path=Path('diagnostic.log'))
        def action():
            app.operation_stage = '恢复开机动画并完整读回校验'
            try:
                raise TimeoutError('command 0x0B at offset 8192 timed out')
            except TimeoutError as error:
                raise RuntimeError('写入中断') from error
        def start_thread(**kwargs):
            return Mock(start=kwargs['target'])
        with patch.object(gui.threading, 'Thread', side_effect=start_thread), \
             self.assertLogs('lg700.app', level='ERROR') as logged:
            gui.App.run(app, action)
        kind, text = app.events.get_nowait()
        self.assertEqual(kind, 'error')
        for expected in ('恢复开机动画', '写入中断', '等待设备响应超时', 'diagnostic.log'):
            self.assertIn(expected, text)
        self.assertNotIn('offset 8192', text)
        self.assertIn('command 0x0B at offset 8192 timed out', '\n'.join(logged.output))
        self.assertEqual(app.events.get_nowait(), ('done',))

    def test_failed_event_does_not_strand_done_or_stop_polling(self):
        app = Mock(events=queue.Queue(), busy=True, buttons=[Mock()],
                   preview_source=None, preview_ready=False)
        app.events.put(('custom_source', 'Z:/missing-media-for-audit.mp4'))
        app.events.put(('done',))
        gui.App.poll(app)
        app.root.report_callback_exception.assert_called_once()
        app.root.after.assert_called_once_with(100, app.poll)
        gui.App.poll(app)
        self.assertFalse(app.busy)
        app.buttons[0].configure.assert_called_once_with(state='normal')

    def test_callback_error_has_cause_and_logs_traceback(self):
        root = SimpleNamespace(_lg700_log_path=Path('app.log'))
        error = ValueError('invalid preview container')
        error.__cause__ = OSError('JPEG decoder rejected entropy stream')
        with patch.object(gui.messagebox, 'showerror') as show, \
             self.assertLogs('lg700.app', level='ERROR') as logged:
            gui.report_callback_error(root, ValueError, error, None)
        text = show.call_args.args[1]
        self.assertIn('预览素材格式无效', text)
        self.assertIn('JPEG 图像解码失败', text)
        self.assertNotIn('JPEG decoder rejected', text)
        self.assertIn('JPEG decoder rejected entropy stream', '\n'.join(logged.output))
        self.assertIn('app.log', text)

    def test_startup_construction_failure_is_reported_and_window_closed(self):
        root = Mock()
        with patch.object(gui.sys, 'argv', ['tool']), \
             patch.object(gui.tk, 'Tk', return_value=root), \
             patch.object(gui, 'needs_startup_notice', return_value=False), \
             patch.object(gui, 'App', side_effect=PermissionError('log directory is read-only')), \
             patch.object(gui, 'report_callback_error') as report:
            self.assertEqual(gui.main(), 2)
        self.assertIn('log directory is read-only', str(report.call_args.args[2]))
        root.destroy.assert_called_once()
        root.mainloop.assert_not_called()


class AssetAuditTests(unittest.TestCase):
    def test_entropy_corruption_that_verify_accepts_is_rejected_before_write(self):
        good = frame(LG700_PROFILE)
        pos = good.index(b'\xff\xda') + 2
        entropy = pos + int.from_bytes(good[pos:pos + 2], 'big')
        corrupt = good[:entropy + 100] + b'\xff\xda' + good[entropy + 102:]
        with Image.open(io.BytesIO(corrupt)) as image:
            image.verify()
        for selector, data in ((0, corrupt), (1, core.pack_boot([corrupt]))):
            with self.subTest(selector=selector):
                device = object.__new__(core.LG700)
                device.query = Mock()
                with self.assertRaisesRegex(ValueError, 'JPEG 解码失败') as raised:
                    device.write_asset(selector, data, 'unused')
                self.assertIsInstance(raised.exception.__cause__, OSError)
                device.query.assert_not_called()

    def test_prefix_must_match_the_native_format_even_with_valid_checksum(self):
        data = bytearray(sample_boot())
        check, size, table, prefix, flags = struct.unpack_from('<IIIHH', data)
        for prefix in (0, 2, 65535):
            with self.subTest(prefix=prefix):
                struct.pack_into('<IIIHH', data, 0,
                    core.checksum_record(size, table, prefix, flags), size, table, prefix, flags)
                with self.assertRaisesRegex(ValueError, '目录前缀无效'):
                    core.unpack_boot(data)

    def test_current_record_cursor_change_blocks_erase_even_when_size_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            d = StoredDevice(LG700_PROFILE)
            d.assets[1] = sample_boot()
            backup = Path(folder) / 'backup'
            with patch.object(core.time, 'sleep'):
                manifest = d.backup(backup)
            current = dict(manifest['assets']['boot'])
            current['next_record'] += core.SECTOR
            d.metadata = Mock(return_value=current)
            d.commands.clear()
            with self.assertRaisesRegex(ValueError, 'next_record 从'):
                d.write_asset(1, core.pack_boot([frame(LG700_PROFILE, 'red')]), backup)
            self.assertEqual(d.commands, [])

    def test_manifest_path_length_and_selector_must_describe_the_actual_file(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'backup'
            directory.mkdir()
            data = sample_boot()
            (directory / 'boot.bin').write_bytes(data)
            (directory.parent / 'outside.bin').write_bytes(data)
            base = dict(file='boot.bin', sha256=core.sha256(data), size=len(data), selector=1)
            cases = [('file', '../outside.bin', '路径超出'), ('size', 1, '长度与清单不一致'),
                     ('selector', 0, '素材区域不匹配')]
            for key, value, expected in cases:
                with self.subTest(key=key):
                    manifest = {'device': {'revision': '0067'},
                                'assets': {'boot': dict(base, **{key: value})}}
                    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
                    with self.assertRaisesRegex(ValueError, expected):
                        core.restore_file(directory, 1)

    def test_hash_correct_but_invalid_backup_cannot_authorize_mode_change(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            data = b'not a native animation'
            (directory / 'boot.bin').write_bytes(data)
            (directory / 'manifest.json').write_text(json.dumps({
                'device': {'revision': '0067'},
                'assets': {'boot': {'file': 'boot.bin', 'sha256': core.sha256(data)}}}), encoding='utf8')
            d = object.__new__(core.LG700)
            d.revision, d.query = 0x67, Mock()
            with self.assertRaisesRegex(ValueError, '动画包太短'):
                d.set_offline_loop(False, directory)
            d.query.assert_not_called()

    def test_windows_redirected_parent_uses_directory_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, redirected = Path(folder) / 'logical', Path(folder) / 'redirected'
            directory.mkdir()
            redirected.mkdir()
            directory, redirected = directory.resolve(), redirected.resolve()
            data = sample_boot()
            (redirected / 'boot.bin').write_bytes(data)
            (directory / 'manifest.json').write_text(json.dumps({
                'device': {'revision': '0067'}, 'assets': {'boot': {
                    'file': 'boot.bin', 'sha256': core.sha256(data)}}}), encoding='utf8')
            real_resolve, real_samefile = Path.resolve, Path.samefile
            def resolve(path, *args, **kwargs):
                if path == directory / 'boot.bin':
                    return redirected / 'boot.bin'
                return real_resolve(path, *args, **kwargs)
            def samefile(path, other):
                if path == directory and other == redirected:
                    return True
                return real_samefile(path, other)
            with patch.object(Path, 'resolve', resolve), patch.object(Path, 'samefile', samefile):
                self.assertEqual(core.restore_file(directory, 1), data)

    def test_original_restore_uses_original_even_when_current_backup_fails(self):
        nested = OSError('current asset read failed')
        nested.__cause__ = TimeoutError('command 0x0D timed out at offset 8192')
        cases = (None, OSError('disk full while saving current assets'),
                 ValueError('current JPEG cannot be decoded'), nested)
        for backup_error in cases:
            with self.subTest(backup_error=str(backup_error)):
                d = Mock(profile=LG700_PROFILE, revision=0x75, info={})
                d.__enter__ = Mock(return_value=d)
                d.__exit__ = Mock(return_value=False)
                d.backup.side_effect = backup_error
                app = Mock(device_identity=None)
                app.target_profile.return_value = LG700_PROFILE
                app.vendor_session.side_effect = nullcontext
                app.run.side_effect = lambda job: job()
                boot = sample_boot()
                standby = core.unpack_boot(boot)[0]
                d.read_asset.return_value = standby, {}
                with patch.object(gui, 'LG700', return_value=d), \
                     patch.object(gui, 'original_directory', return_value=Path('original')), \
                     patch.object(gui, 'restore_manifest', return_value={}), \
                     patch.object(gui, 'restore_file', side_effect=lambda p, s, **k: boot if s else standby), \
                     patch.object(gui, 'new_snapshot', return_value=Path('fresh')), \
                     patch.object(gui.time, 'sleep'):
                    gui.App.restore(app)
                d.set_offline_loop.assert_called_once_with(False, Path('original'))
                self.assertEqual(len(d.write_asset.call_args_list), 2)
                for call, selector, data in zip(d.write_asset.call_args_list, (1, 0), (boot, standby)):
                    self.assertEqual(call.args[:3], (selector, data, Path('original')))
                    self.assertTrue(call.kwargs['recovery'])
                d.read_asset.assert_called_once_with(0)
                if backup_error is not None:
                    messages = '\n'.join(str(c.args[0]) for c in app.note.call_args_list)
                    self.assertIn(concise_error(backup_error), messages)
                    self.assertIn('将用已核验的原厂备份恢复', messages)

    def test_damaged_current_assets_are_actually_replaced_by_original(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key), tempfile.TemporaryDirectory() as folder, \
                 patch.object(core.time, 'sleep'):
                root = Path(folder)
                d = StoredDevice(profile)
                d.assets[1] = core.pack_boot([frame(profile, 'orange')])
                original = dict(d.assets)
                source = root / 'Original'
                d.backup(source)
                source_files = {p.name: p.read_bytes() for p in source.iterdir()}
                # A valid-size but invalid container causes the real backup
                # code to fail. Recovery still uses the intact original.
                d.assets[1] = b'damaged current animation container'
                d.commands.clear()
                app = Mock(device_identity=None)
                app.target_profile.return_value = profile
                app.vendor_session.side_effect = nullcontext
                app.run.side_effect = lambda job: job()
                with patch.object(gui, 'LG700', return_value=d), \
                     patch.object(gui, 'original_directory', return_value=source), \
                     patch.object(gui, 'new_snapshot', return_value=root / 'failed-current-backup'):
                    gui.App.restore(app)
                self.assertEqual(d.assets, original)
                self.assertEqual([payload for op, payload in d.commands if op == 0x1E], [b'\0'])
                self.assertEqual(sum(op == 0x0A for op, _ in d.commands), 2)
                for name, data in source_files.items():
                    self.assertEqual((source / name).read_bytes(), data)
                self.assertTrue(any('将用已核验的原厂备份恢复' in str(c.args[0])
                                    for c in app.note.call_args_list))

    def test_invalid_original_cannot_start_restore(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(core.time, 'sleep'):
            d = StoredDevice(LG700_PROFILE)
            d.assets[1] = sample_boot()
            source = Path(folder) / 'Original'
            d.backup(source)
            (source / 'boot.bin').write_bytes(b'damaged original backup')
            d.commands.clear()
            app = Mock(device_identity=None)
            app.target_profile.return_value = LG700_PROFILE
            app.vendor_session.side_effect = nullcontext
            with patch.object(gui, 'LG700', return_value=d), \
                 patch.object(gui, 'original_directory', return_value=source), \
                 patch.object(gui.messagebox, 'showerror') as show:
                gui.App.restore(app)
            self.assertIn('备份文件校验失败', show.call_args.args[1])
            self.assertNotIn('SHA-256', show.call_args.args[1])
            app.run.assert_not_called()
            self.assertEqual(d.commands, [])


class PacketDetailTests(unittest.TestCase):
    def target(self, reports):
        d = object.__new__(core.LG700)
        d.hid = Mock()
        d.hid.write.return_value = 441
        d.hid.read.side_effect = reports
        return d

    def test_short_output_names_command_and_length_without_replay(self):
        d = self.target([])
        d.hid.write.return_value = 12
        with self.assertRaisesRegex(OSError, '0x0A.*441.*12'):
            d.query(0x0A, struct.pack('<II', 1, 8192))
        d.hid.write.assert_called_once()
        d.hid.read.assert_not_called()

    def test_short_input_is_distinguished_from_no_response(self):
        for report, expected in ((b'', '等待响应超时'), (b'short', '收到 5 字节')):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(core.ResponseSyncError if report else TimeoutError, expected):
                    self.target([report]).query(0x0A, struct.pack('<II', 1, 8192))

    def test_wrong_continuation_names_actual_and_expected_header(self):
        reports = [struct.pack('<I', 0x8000020D).ljust(440, b'\0'),
                   struct.pack('<I', 0x0000020D).ljust(440, b'\0')]
        d = self.target(reports)
        with self.assertRaisesRegex(core.ResponseSyncError, '0x0000010D.*0x0000020D'):
            d.query(0x0D, struct.pack('<IIi', 0, 500, 0))
        d.hid.write.assert_called_once()

    def test_write_rejection_explains_acknowledged_selector_and_size(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(core.time, 'sleep'):
            d = StoredDevice(LG700_PROFILE)
            backup = Path(folder) / 'backup'
            d.backup(backup)
            query = d.query
            def reject(op, payload=b'', **kw):
                if op == 0x0A:
                    return struct.pack('<Ii', 0, -1)
                return query(op, payload, **kw)
            d.query = Mock(side_effect=reject)
            with self.assertRaisesRegex(OSError, '请求 selector=1.*返回 selector=0, size=-1'):
                d.write_asset(1, sample_boot(), backup)
            self.assertNotIn(0x0B, [call.args[0] for call in d.query.call_args_list])


if __name__ == '__main__':
    unittest.main()
