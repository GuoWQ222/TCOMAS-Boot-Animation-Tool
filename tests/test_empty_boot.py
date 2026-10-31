"""Empty native boot records: synthetic storage, no physical USB access."""
from contextlib import nullcontext
import io
import json
from pathlib import Path
import queue
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import backup_store
import lg700_core as core
import lg700_tool as gui
from device_profiles import LG600_PROFILE, PROFILES


def frame(profile, color='#437ca3'):
    output = io.BytesIO()
    Image.new('RGB', profile.size, color).save(output, 'JPEG', quality=75)
    return output.getvalue()


class StoredDevice(core.LG700):
    """Run actual metadata/backup/write methods against synthetic flash slots."""
    def __init__(self, profile=LG600_PROFILE):
        self._profile, self.revision = profile, 0x75
        self.hid = self._mutex = self._kernel = None
        self.info = dict(vendor_id=profile.vid, product_id=profile.pid,
            product_string=profile.name, serial_number='synthetic-empty-boot',
            path=b'offline-empty-boot', interface_number=0, usage_page=0xFF06, usage=1)
        self.assets = {1: None, 0: frame(profile, '#a34c43')}
        self.selected = self.pending = None
        self.commands = []
        self.fail_write = False

    def _query(self, op, payload=b'', timeout_ms=6000):
        self.commands.append((op, payload))
        if op == 0x87:
            return struct.pack('<H', self.revision)
        if op == 0x0C:
            selector, reserved = struct.unpack('<II', payload)
            assert reserved == 0
            self.selected = selector
            begin, _ = self.profile.regions[selector]
            data = self.assets[selector]
            if data is None:
                return struct.pack('<IiIII', selector, 0, 0xFFFFFFFF, begin, 0)
            end = begin + ((len(data) + 20 + core.SECTOR - 1) // core.SECTOR) * core.SECTOR
            return struct.pack('<IiIII', selector, len(data), begin, end, 0)
        if op == 0x0D:
            offset, count, reserved = struct.unpack('<IIi', payload)
            assert reserved == 0 and self.assets[self.selected] is not None
            content = self.assets[self.selected][offset:offset + count]
            assert len(content) == count
            return struct.pack('<IIi', offset, count, 0) + content
        if op == 0x1E:
            assert payload in (b'\0', b'\1')
            return b''
        if op == 0x0A:
            selector, size = struct.unpack('<II', payload)
            self.pending = dict(selector=selector, size=size, data=bytearray())
            self.assets[selector] = None
            return struct.pack('<Ii', selector, size)
        if op == 0x0B:
            if self.fail_write:
                raise TimeoutError('synthetic write interruption')
            offset, count, percent = struct.unpack_from('<III', payload)
            assert offset == len(self.pending['data']) and len(payload) == 12 + count
            self.pending['data'].extend(payload[12:])
            if percent == 100:
                assert len(self.pending['data']) == self.pending['size']
                self.assets[self.pending['selector']] = bytes(self.pending['data'])
                self.pending = None
            return struct.pack('<IIi', offset, count, 0)
        raise AssertionError(f'Unexpected command: {op:#x}')


class EmptyBootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.device = StoredDevice()
        sleeper = patch('lg700_core.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def snapshot(self, name='snapshot'):
        path = self.folder / name
        self.device.backup(path)
        return path

    def app(self):
        app = Mock(busy=False, events=queue.Queue(), device_identity=None, preview_size=(480, 480))
        app.target_profile.return_value = self.device.profile
        app.model_choice.get.return_value = '自动识别'
        app.run.side_effect = lambda job: job()
        app.vendor_session.return_value = nullcontext()
        app.enable_offline.side_effect = lambda device, directory: device.set_offline_loop(True, directory)
        return app

    def assert_no_changes(self):
        self.assertFalse(any(op in (0x0A, 0x0B, 0x1E) for op, _ in self.device.commands))

    def test_only_stable_complete_empty_signature_is_accepted_for_each_profile(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key):
                device = StoredDevice(profile)
                meta = device.metadata(1)
                self.assertTrue(meta['empty'])
                self.assertTrue(core.is_empty_boot(meta, profile))
                self.assertEqual([op for op, _ in device.commands], [0x0C, 0x0C])

    def test_zero_lengths_with_unknown_fields_are_still_rejected(self):
        variants = [(1, 0, 0, 0x400000, 0), (1, 0, 0x400000, 0x400000, 0),
                    (1, 0, 0xFFFFFFFF, 0x410000, 0), (1, 0, 0xFFFFFFFF, 0x400000, 1),
                    (0, 0, 0xFFFFFFFF, 0x400000, 0), (1, -1, 0xFFFFFFFF, 0x400000, 0),
                    (1, core.capacity(1) + 1, 0x400000, 0xF00000, 0)]
        for values in variants:
            with self.subTest(values=values), patch.object(self.device, 'query',
                    return_value=struct.pack('<IiIII', *values)) as query:
                with self.assertRaises(OSError):
                    self.device.metadata(1)
                query.assert_called_once()
        self.device.assets[0] = None
        with self.assertRaisesRegex(OSError, 'selector=0, size=0'):
            self.device.metadata(0)
        self.assert_no_changes()

    def test_changed_empty_confirmation_aborts(self):
        empty = struct.pack('<IiIII', 1, 0, 0xFFFFFFFF, 0x400000, 0)
        changed = struct.pack('<IiIII', 1, 100, 0x400000, 0x410000, 0)
        with patch.object(self.device, 'query', side_effect=[empty, changed]) as query:
            with self.assertRaisesRegex(OSError, '元数据发生变化'):
                self.device.metadata(1)
            self.assertEqual(query.call_count, 2)

    def test_empty_read_never_issues_payload_reads_and_first_frame_is_explicit(self):
        data, meta = self.device.read_asset(1)
        self.assertEqual(data, b'')
        self.assertTrue(meta['empty'])
        with self.assertRaisesRegex(ValueError, '尚未保存开机动画'):
            self.device.read_boot_first_frame()
        self.assertFalse(any(op == 0x0D for op, _ in self.device.commands))
        self.assert_no_changes()

    def test_backup_preserves_empty_state_and_real_standby(self):
        path = self.snapshot()
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf8'))
        self.assertTrue(manifest['assets']['boot']['empty'])
        self.assertEqual(manifest['assets']['boot']['sha256'], core.sha256(b''))
        self.assertEqual((path / 'boot.bin').read_bytes(), b'')
        self.assertEqual(core.restore_file(path, 0), self.device.assets[0])
        self.assertEqual(core.restore_file(path, 1, allow_empty=True), b'')
        self.assert_no_changes()

    def test_empty_backup_cannot_be_restored_as_an_animation(self):
        path = self.snapshot()
        with self.assertRaisesRegex(ValueError, '没有可恢复的动画'):
            core.restore_file(path, 1)
        self.device.commands.clear()
        with self.assertRaisesRegex(ValueError, '无法恢复不存在的动画'):
            self.device.write_asset(1, core.pack_boot([frame(self.device.profile)]), path, recovery=True)
        self.assert_no_changes()

    def test_tampered_empty_backup_blocks_even_mode_commands(self):
        path = self.snapshot()
        original = json.loads((path / 'manifest.json').read_text(encoding='utf8'))
        for field, value in [('active_record', 0x400000), ('next_record', 0x410000),
                             ('extra', 1), ('empty', False), ('sha256', 'wrong')]:
            with self.subTest(field=field):
                manifest = json.loads(json.dumps(original))
                manifest['assets']['boot'][field] = value
                (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
                self.device.commands.clear()
                with self.assertRaises(ValueError):
                    self.device.set_offline_loop(False, path)
                with self.assertRaises(ValueError):
                    core.restore_file(path, 1, allow_empty=True)
                self.assert_no_changes()

    def test_nonempty_file_cannot_be_marked_as_empty(self):
        path = self.snapshot()
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf8'))
        (path / 'boot.bin').write_bytes(b'not empty')
        manifest['assets']['boot']['sha256'] = core.sha256(b'not empty')
        (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
        with self.assertRaisesRegex(ValueError, '空动画槽记录无效'):
            self.device.set_offline_loop(False, path)
        self.assert_no_changes()

    def test_failed_standby_validation_still_blocks_backup(self):
        self.device.assets[0] = b'not a JPEG'
        with self.assertRaises(OSError):
            self.device.backup(self.folder / 'invalid')
        self.assertFalse((self.folder / 'invalid' / 'manifest.json').exists())
        self.assert_no_changes()

    def test_initial_empty_original_is_preserved_after_later_custom_writes(self):
        path = self.snapshot()
        with patch.object(backup_store, 'data_home', return_value=self.folder / 'store'):
            original = backup_store.preserve_original(self.device, path)
            original_manifest = (original / 'manifest.json').read_bytes()
            self.device.assets[1] = core.pack_boot([frame(self.device.profile)])
            later = self.snapshot('later')
            self.assertEqual(backup_store.preserve_original(self.device, later), original)
            self.assertEqual((original / 'manifest.json').read_bytes(), original_manifest)
            self.assertEqual(backup_store.original_directory(self.device), original)
        self.assertEqual(core.restore_file(original, 1, allow_empty=True), b'')

    def test_full_first_write_backs_up_writes_verifies_and_then_enables_loop(self):
        app = self.app()
        data = core.pack_boot([frame(self.device.profile)] * 3)
        old_standby = self.device.assets[0]
        snapshot = self.folder / 'first-write'
        with patch.object(gui, 'LG700', return_value=self.device), \
             patch.object(gui, 'new_snapshot', return_value=snapshot), \
             patch.object(backup_store, 'data_home', return_value=self.folder / 'store'):
            gui.App.write_data(app, data, profile=self.device.profile)
        self.assertEqual(self.device.assets[1], data)
        self.assertEqual(self.device.assets[0], core.unpack_boot(data, profile=self.device.profile)[0])
        self.assertEqual((snapshot / 'standby.bin').read_bytes(), old_standby)
        self.assertEqual((snapshot / 'boot.bin').read_bytes(), b'')
        changes = [(op, payload) for op, payload in self.device.commands if op in (0x1E, 0x0A)]
        self.assertEqual([(op, payload[0]) for op, payload in changes],
                         [(0x1E, 0), (0x0A, 1), (0x0A, 0), (0x1E, 1)])
        mode1 = next(index for index, command in enumerate(self.device.commands) if command == (0x1E, b'\1'))
        self.assertEqual([op for op, _ in self.device.commands[mode1 + 1:]], [0x87])
        audit = json.loads((snapshot / 'playback_mode.json').read_text(encoding='utf8'))
        self.assertTrue(audit['communication_verified'])
        self.assertFalse(audit['visual_playback_verified'])

    def test_failed_first_write_is_not_retried_or_resumed(self):
        app = self.app()
        data = core.pack_boot([frame(self.device.profile)])
        self.device.fail_write = True
        with patch.object(gui, 'LG700', return_value=self.device), \
             patch.object(gui, 'new_snapshot', return_value=self.folder / 'failed-write'), \
             patch.object(backup_store, 'data_home', return_value=self.folder / 'store'):
            with self.assertRaisesRegex(OSError, '未自动重发'):
                gui.App.write_data(app, data, profile=self.device.profile)
        self.assertEqual([payload for op, payload in self.device.commands if op == 0x1E], [b'\0'])
        self.assertEqual(sum(op == 0x0A for op, _ in self.device.commands), 1)
        self.assertEqual(sum(op == 0x0B for op, _ in self.device.commands), 1)

    def test_slot_becoming_nonempty_after_backup_aborts_before_erase(self):
        path = self.snapshot()
        data = core.pack_boot([frame(self.device.profile)])
        self.device.assets[1] = data
        self.device.commands.clear()
        with self.assertRaisesRegex(ValueError, '备份后素材发生变化'):
            self.device.write_asset(1, data, path)
        self.assert_no_changes()

    def test_empty_new_asset_and_unknown_revision_remain_blocked(self):
        path = self.snapshot()
        self.device.commands.clear()
        with self.assertRaises(ValueError):
            self.device.write_asset(1, b'', path)
        self.device.revision = 0x76
        with self.assertRaisesRegex(RuntimeError, '尚未适配'):
            self.device.write_asset(1, core.pack_boot([frame(self.device.profile)]), path)
        self.assert_no_changes()

    def test_gui_reports_connected_empty_slot_without_reading_a_first_frame(self):
        app = self.app()
        with patch.object(gui, 'LG700', return_value=self.device):
            gui.App.detect(app)
        events = list(app.events.queue)
        self.assertTrue(any(event[0] == 'device' for event in events))
        self.assertIn(('boot_empty',), events)
        self.assertFalse(any(event[0] == 'media_missing' for event in events))
        self.assertFalse(any(op == 0x0D for op, _ in self.device.commands))
        self.assert_no_changes()

    def test_gui_empty_state_has_a_clear_first_write_message(self):
        app = self.app()
        app.model_summary = 'LG600 · REV 0075'
        app.events.put(('boot_empty',))
        with patch.object(gui, 'theme_font', return_value=('Segoe UI', 16)):
            gui.App.poll(app)
        app.quality_size.set.assert_called_once_with('硬件动画：未保存')
        app.file_text.set.assert_called_once_with('设备已连接，开机动画为空；可选择本地图片或视频写入')

    def test_empty_slot_cannot_enable_or_repair_playback_without_an_animation(self):
        path = self.snapshot()
        with self.assertRaisesRegex(ValueError, '请先写入'):
            self.device.set_offline_loop(True, path)
        app = self.app()
        with patch.object(gui, 'LG700', return_value=self.device):
            with self.assertRaisesRegex(ValueError, '请先写入'):
                gui.App.repair_offline(app)
        self.assert_no_changes()

    def test_restore_of_empty_original_stops_before_scheduling_a_write(self):
        path = self.snapshot()
        app = self.app()
        with patch.object(gui, 'LG700', return_value=self.device), \
             patch.object(gui, 'original_directory', return_value=path), \
             patch.object(gui.filedialog, 'askdirectory') as choose, \
             patch.object(gui.messagebox, 'showerror') as error:
            gui.App.restore(app)
        choose.assert_not_called()
        error.assert_called_once()
        self.assertIn('没有可恢复的动画', error.call_args.args[1])
        app.run.assert_not_called()
        self.assert_no_changes()

    def test_cli_returns_device_and_standby_information_for_an_empty_boot_slot(self):
        output = self.folder / 'inspect.json'
        with patch.object(sys, 'argv', ['tool', '--inspect', '--model', 'lg600', '--output', str(output)]), \
             patch.object(gui, 'LG700', return_value=self.device), \
             patch.object(gui.tk, 'Tk') as window:
            status = gui.main()
        self.assertEqual(status, 0)
        window.assert_not_called()
        result = json.loads(output.read_text(encoding='utf8'))
        self.assertEqual(result['model'], 'TCOMAS LG600')
        self.assertTrue(result['boot']['empty'])
        self.assertGreater(result['standby']['size'], 0)
        self.assert_no_changes()


if __name__ == '__main__':
    unittest.main()
