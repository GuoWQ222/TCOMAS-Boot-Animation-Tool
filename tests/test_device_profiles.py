"""Model routing and write barriers; synthetic files and mocked USB only."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import queue
import struct
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import backup_store
import convert_media
import current_media
import lg700_core
from device_profiles import (PROFILES, LG700_PROFILE, LG600_PROFILE, LX800_PROFILE,
    connection_identity, backup_device_key, check_manifest_device, manifest_profile)
from lg700_core import LG700, pack_boot, unpack_boot, validate_asset, sha256, restore_file
from lg700_tool import App
from quality_preview import QualityPreview
from preview_geometry import preview_frame


def info(profile, serial='synthetic-display', interface=0):
    return dict(vendor_id=profile.vid, product_id=profile.pid,
                interface_number=interface, usage_page=0xFF06, usage=1,
                path=f'offline-{profile.key}-{serial}'.encode(),
                product_string=profile.name, serial_number=serial)


def media(profile):
    output = io.BytesIO()
    Image.new('RGB', profile.size, '#387cba').save(output, 'JPEG')
    return pack_boot([output.getvalue()])


class DeviceProfileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        convert_media._conversion_cache.clear()

    def usb(self, devices):
        stack = ExitStack()
        self.addCleanup(stack.close)
        hid = types.SimpleNamespace(enumerate=Mock(side_effect=lambda vid, pid:
            [d for d in devices if (d['vendor_id'], d['product_id']) == (vid, pid)]))
        stack.enter_context(patch.dict('sys.modules', {'hid': hid}))
        stack.enter_context(patch.object(lg700_core, 'os', types.SimpleNamespace(name='offline')))
        opened = stack.enter_context(patch.object(lg700_core, 'open_command_transport'))
        stack.enter_context(patch.object(LG700, 'query', return_value=struct.pack('<H', 0x75)))
        return opened, hid

    def test_auto_detection_routes_each_pid_and_ignores_streaming_interface(self):
        for profile in PROFILES:
            with self.subTest(model=profile.key):
                opened, _ = self.usb([info(profile), info(profile, interface=1)])
                with LG700() as device:
                    self.assertEqual(device.profile, profile)
                    opened.assert_called_once_with(info(profile)['path'])

    def test_ambiguous_devices_never_open_until_a_model_is_selected(self):
        opened, _ = self.usb([info(LG600_PROFILE), info(LX800_PROFILE)])
        with self.assertRaisesRegex(RuntimeError, '2 台'):
            LG700()
        opened.assert_not_called()
        with LG700(profile=LX800_PROFILE) as device:
            self.assertEqual(device.profile, LX800_PROFILE)

    def test_two_devices_of_one_model_still_require_disambiguation(self):
        opened, _ = self.usb([info(LX800_PROFILE, 'a'), info(LX800_PROFILE, 'b')])
        with self.assertRaisesRegex(RuntimeError, '2 台'):
            LG700(profile=LX800_PROFILE)
        opened.assert_not_called()

    def test_changed_target_is_rejected_before_opening_usb(self):
        opened, _ = self.usb([info(LX800_PROFILE, 'new')])
        with self.assertRaisesRegex(RuntimeError, '目标设备已变化'):
            LG700(profile=LX800_PROFILE, expected_identity=connection_identity(info(LX800_PROFILE, 'old')))
        opened.assert_not_called()

    def test_reconnect_keeps_model_serial_and_revision(self):
        for profile in (LG600_PROFILE, LX800_PROFILE):
            with self.subTest(model=profile.key):
                opened, hid = self.usb([info(profile)])
                device = object.__new__(LG700)
                device.info, device._profile, device.revision = info(profile), profile, 0x75
                device.hid = Mock()
                device._reconnect_command_interface()
                hid.enumerate.assert_called_with(profile.vid, profile.pid)
                opened.assert_called_once_with(info(profile)['path'])

    def test_unknown_new_model_revision_cannot_write_or_change_mode(self):
        for profile in (LG600_PROFILE, LX800_PROFILE):
            device = object.__new__(LG700)
            device._profile, device.revision = profile, 0x67
            device.query = Mock()
            with self.assertRaisesRegex(RuntimeError, '尚未适配'):
                device.write_asset(1, media(profile), self.folder)
            with self.assertRaisesRegex(RuntimeError, '尚未适配'):
                device.set_offline_loop(True, self.folder)
            device.query.assert_not_called()

    def test_conversion_uses_target_dimensions_and_rejects_wrong_shape(self):
        source = self.folder / 'wide.png'
        Image.new('RGB', (960, 480), '#387cba').save(source)
        for profile in PROFILES:
            for fill in (False, True):
                with self.subTest(model=profile.key, fill=fill):
                    asset = convert_media.build_asset(source, fill_screen=fill, profile=profile)
                    frame = unpack_boot(asset, profile=profile)[0]
                    with Image.open(io.BytesIO(frame)) as image:
                        self.assertEqual(image.size, profile.size)
                        if not fill:
                            self.assertLess(max(image.getpixel((10, 10))), 5)
                        else:
                            self.assertGreater(max(image.getpixel((10, 10))), 30)
                    validate_asset(0, frame, profile=profile)
        with self.assertRaisesRegex(ValueError, '640×480'):
            validate_asset(1, media(LX800_PROFILE), profile=LG700_PROFILE)
        with self.assertRaisesRegex(ValueError, '480×480'):
            validate_asset(1, media(LG700_PROFILE), profile=LX800_PROFILE)

    def test_conversion_cache_and_worker_include_model(self):
        source = self.folder / 'square.png'
        Image.new('RGB', (480, 480)).save(source)
        with patch.object(convert_media, 'build_asset', wraps=convert_media.build_asset) as build:
            for profile in (LG600_PROFILE, LX800_PROFILE, LG700_PROFILE):
                convert_media.cached_build_asset(source, profile=profile)
                convert_media.cached_build_asset(source, profile=profile)
            self.assertEqual(build.call_count, 3)
        events = queue.Queue()
        worker = QualityPreview(events)
        self.addCleanup(worker.close)
        worker.request(1, source, 75, False, LX800_PROFILE)
        result = events.get(timeout=10)
        self.assertEqual(result[7], 'lx800')
        self.assertIsNone(result[6])
        unpack_boot(result[5], profile=LX800_PROFILE)

    def test_gui_drops_a_preview_from_previous_model(self):
        app = Mock(busy=False, preview_generation=2, preview_source='a.mp4')
        app.target_profile.return_value = LX800_PROFILE
        app.selected_quality.return_value = 75
        app.fill_screen.get.return_value = False
        App.accept_quality_result(app, ('quality_result', 2, 'a.mp4', 75, False, b'old', None, 'lg600'))
        app.apply_preview_data.assert_not_called()

    def test_round_panel_preview_masks_corners_without_changing_encoded_data(self):
        data = media(LX800_PROFILE)
        before = sha256(data)
        jpeg = unpack_boot(data, profile=LX800_PROFILE)[0]
        circular = preview_frame(jpeg, LX800_PROFILE, (360, 360))
        square = preview_frame(jpeg, LG600_PROFILE, (360, 360))
        self.assertEqual(circular.getpixel((0, 0)), (21, 27, 36))
        self.assertNotEqual(square.getpixel((0, 0)), (21, 27, 36))
        self.assertEqual(circular.getpixel((180, 180)), square.getpixel((180, 180)))
        self.assertEqual(sha256(data), before)

    def test_backup_paths_separate_models_and_preserve_lg700_path(self):
        paths = []
        with patch.object(backup_store, 'data_home', return_value=self.folder):
            for profile in PROFILES:
                device = types.SimpleNamespace(info=info(profile), profile=profile)
                path = backup_store.device_home(device)
                paths.append(path)
                if profile == LG700_PROFILE:
                    self.assertEqual(path.parent, self.folder / 'Backups')
                else:
                    self.assertEqual(path.parent.name, profile.name)
        self.assertEqual(len(set(paths)), 3)

    def manifest(self, profile):
        return {'device': {'model': profile.key, 'pid': f'{profile.pid:04X}',
            'vid': f'{profile.vid:04X}', 'size': list(profile.size), 'revision': '0075',
            'device_key': backup_device_key(info(profile), profile)}, 'assets': {}}

    def test_manifest_blocks_cross_model_and_cross_device_restore(self):
        manifest = self.manifest(LG600_PROFILE)
        with self.assertRaisesRegex(ValueError, '跨型号'):
            check_manifest_device(manifest, LX800_PROFILE)
        with self.assertRaisesRegex(ValueError, '不属于当前设备'):
            check_manifest_device(manifest, LG600_PROFILE, info=info(LG600_PROFILE, 'other'))
        manifest['device']['pid'] = '151D'
        with self.assertRaisesRegex(ValueError, 'PID 不一致'):
            manifest_profile(manifest)
        self.assertEqual(manifest_profile({'device': {'revision': '0067'}}), LG700_PROFILE)

    def test_cross_model_mode_command_is_blocked_before_usb(self):
        (self.folder / 'manifest.json').write_text(json.dumps(self.manifest(LG600_PROFILE)))
        device = object.__new__(LG700)
        device._profile, device.revision, device.info = LX800_PROFILE, 0x75, info(LX800_PROFILE)
        device.query = Mock()
        with self.assertRaisesRegex(ValueError, '跨型号'):
            device.set_offline_loop(False, self.folder)
        device.query.assert_not_called()

    def test_restore_checks_model_dimensions_and_hash(self):
        for profile in (LG600_PROFILE, LX800_PROFILE):
            data = media(profile)
            manifest = self.manifest(profile)
            manifest['assets']['boot'] = {'file': 'boot.bin', 'sha256': sha256(data)}
            (self.folder / 'boot.bin').write_bytes(data)
            (self.folder / 'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(restore_file(self.folder, 1, profile=profile), data)
            with self.assertRaisesRegex(ValueError, '不匹配'):
                restore_file(self.folder, 1, profile=LG700_PROFILE)
            (self.folder / 'boot.bin').write_bytes(data[:-1])
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                restore_file(self.folder, 1, profile=profile)

    def test_vendor_selection_uses_only_the_selected_model_key(self):
        source = self.folder / 'video.mp4'
        source.write_bytes(b'placeholder')
        state = {'data': {'currentPreset': {'nativePath': str(source)}}}
        for profile in PROFILES:
            with patch.object(current_media.Path, 'exists', return_value=True), \
                 patch.object(current_media, 'local_storage', return_value=state) as storage:
                self.assertEqual(current_media.detect_current_media(profile)['path'], str(source))
                self.assertEqual(storage.call_args.args[1], profile.key + '_lastPageState')


if __name__ == '__main__':
    unittest.main()
