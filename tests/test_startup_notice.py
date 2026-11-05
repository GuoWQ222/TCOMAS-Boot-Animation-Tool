"""Reading the landing page must never start device/process operations."""
import unittest
from unittest.mock import Mock, patch

import lg700_tool
from device_profiles import LG600_PROFILE
from startup_notice import StartupNotice


class StartupNoticeTests(unittest.TestCase):
    def test_main_defers_app_and_hardware_until_read(self):
        root = Mock()
        with patch.object(lg700_tool.sys, 'argv', ['tool', '--model', 'lg600']), \
             patch.object(lg700_tool.tk, 'Tk', return_value=root), \
             patch.object(lg700_tool, 'StartupNotice') as page, \
             patch.object(lg700_tool, 'App') as app, \
             patch.object(lg700_tool, 'LG700') as device, \
             patch.object(lg700_tool, 'pause_vendor') as pause, \
             patch.object(lg700_tool, 'needs_startup_notice', return_value=True), \
             patch.object(lg700_tool, 'remember_notice_read') as remember:
            lg700_tool.main()
            app.assert_not_called()
            device.assert_not_called()
            pause.assert_not_called()
            remember.assert_not_called()
            root.mainloop.assert_called_once()
            self.assertEqual(page.call_args.args, (root,))
            self.assertEqual(page.call_args.kwargs['version'], lg700_tool.APP_VERSION)
            page.call_args.kwargs['on_read']()
            remember.assert_called_once()
            app.assert_called_once_with(root, profile=LG600_PROFILE, first_run=True)

    def test_closing_startup_window_does_not_construct_main_app(self):
        root = Mock()
        root.mainloop.side_effect = root.destroy
        with patch.object(lg700_tool.sys, 'argv', ['tool']), \
             patch.object(lg700_tool.tk, 'Tk', return_value=root), \
             patch.object(lg700_tool, 'StartupNotice'), \
             patch.object(lg700_tool, 'App') as app, \
             patch.object(lg700_tool, 'pause_vendor') as pause, \
             patch.object(lg700_tool, 'needs_startup_notice', return_value=True), \
             patch.object(lg700_tool, 'remember_notice_read') as remember:
            lg700_tool.main()
        root.destroy.assert_called_once()
        app.assert_not_called()
        pause.assert_not_called()
        remember.assert_not_called()

    def test_already_read_starts_main_without_creating_notice(self):
        root = Mock()
        with patch.object(lg700_tool.sys, 'argv', ['tool']), \
             patch.object(lg700_tool.tk, 'Tk', return_value=root), \
             patch.object(lg700_tool, 'needs_startup_notice', return_value=False), \
             patch.object(lg700_tool, 'StartupNotice') as page, \
             patch.object(lg700_tool, 'App') as app:
            lg700_tool.main()
        page.assert_not_called()
        app.assert_called_once_with(root, profile=None)
        root.mainloop.assert_called_once()

    def page(self):
        page = object.__new__(StartupNotice)
        page._entered = False
        page.root, page.frame, page.read_button, page.on_read = Mock(), Mock(), Mock(), Mock()
        return page

    def test_firmware_upgrade_opens_official_page_only_when_requested(self):
        root = Mock()
        url = 'https://www.tcomas.cn/FirmwareUpgradeTest'
        with patch.object(lg700_tool, 'confirm_warning', side_effect=[False, True, True]) as warn, \
             patch.object(lg700_tool.webbrowser, 'open', side_effect=[True, False]) as browse, \
             patch.object(lg700_tool.messagebox, 'showerror') as error:
            lg700_tool.show_firmware_upgrade(root, 'LG700', 0x67)
            browse.assert_not_called()
            message = warn.call_args.kwargs['message']
            self.assertIn('REV 0067', message)
            self.assertIn('请选择 TCOMAS_LG700_V75，否则本软件可能不会正常工作。', message)
            self.assertIn(url, message)
            lg700_tool.show_firmware_upgrade(root, 'LG700', 0x67)
            browse.assert_called_once_with(url)
            lg700_tool.show_firmware_upgrade(root, 'LG700', 0x67)
            self.assertIn(url, error.call_args.args[1])

    def test_acknowledgement_opens_main_once_even_if_called_again(self):
        page = self.page()
        page.accept()
        page.accept()
        page.on_read.assert_called_once()
        page.frame.destroy.assert_called_once()
        self.assertTrue(page._entered)

    def test_failed_transition_keeps_notice_available_for_retry(self):
        page = self.page()
        page.on_read.side_effect = [RuntimeError('test init failure'), None]
        with patch('startup_notice.messagebox.showerror') as error:
            page.accept()
        self.assertFalse(page._entered)
        page.frame.destroy.assert_not_called()
        page.frame.pack.assert_called_once_with(fill='both', expand=True)
        error.assert_called_once()
        page.accept()
        page.frame.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
