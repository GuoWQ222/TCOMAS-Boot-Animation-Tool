"""Temporary vendor suspension always unwinds and blocks I/O on failure."""
from contextlib import contextmanager
from types import MethodType
import unittest
from unittest.mock import Mock, patch
from lg700_tool import App
from device_profiles import LG700_PROFILE
from sample_assets import sample_boot


class VendorSessionTests(unittest.TestCase):
    def test_device_failure_still_restores_temporary_pause(self):
        order = []
        @contextmanager
        def pause():
            order.append('pause')
            try:
                yield
            finally:
                order.append('resume')
        with patch('lg700_tool.pause_vendor', pause):
            with self.assertRaisesRegex(OSError, 'device error'):
                with App.vendor_session(Mock()):
                    order.append('operation')
                    raise OSError('device error')
        self.assertEqual(order, ['pause', 'operation', 'resume'])

    def test_failed_pause_prevents_opening_or_writing_device(self):
        @contextmanager
        def denied():
            raise OSError('pause denied')
            yield
        app = Mock(device_identity=None)
        app.target_profile.return_value = LG700_PROFILE
        app.vendor_session = MethodType(App.vendor_session, app)
        with patch('lg700_tool.pause_vendor', denied), patch('lg700_tool.LG700') as device:
            with self.assertRaisesRegex(OSError, 'pause denied'):
                App.write_data(app, sample_boot())
        device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
