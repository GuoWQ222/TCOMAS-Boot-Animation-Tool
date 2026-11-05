"""Host process failures stay visible; fake Win32 APIs only."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from error_details import describe_error
import vendor_pause


class VendorErrorDetailsTests(unittest.TestCase):
    def test_process_query_keeps_exit_status_and_stderr(self):
        result = SimpleNamespace(returncode=5, stderr='CIM query access denied', stdout='')
        with patch.object(vendor_pause.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(OSError, '退出码 5.*CIM query access denied'):
                vendor_pause.vendor_processes()

    def test_open_process_failure_keeps_windows_error(self):
        kernel, native = Mock(), Mock()
        kernel.OpenProcess.return_value = 0
        processes = [dict(ProcessId=42, ExecutablePath='C:/synthetic/vendor.exe')]
        with patch.object(vendor_pause, 'vendor_processes', return_value=processes), \
             patch.object(vendor_pause.ctypes, 'WinDLL', side_effect=[kernel, native]), \
             patch.object(vendor_pause.ctypes, 'get_last_error', return_value=5):
            with self.assertRaises(OSError) as raised:
                with vendor_pause.pause_vendor():
                    self.fail('must not start device work')
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertIn('5', describe_error(raised.exception))
        native.NtSuspendProcess.assert_not_called()

    def test_resume_error_keeps_operation_cause_and_closes_every_handle(self):
        kernel, native = Mock(), Mock()
        kernel.OpenProcess.side_effect = [10, 20]
        def query(handle, flags, path, length):
            path.value = 'C:/synthetic/vendor.exe'
            return True
        kernel.QueryFullProcessImageNameW.side_effect = query
        native.NtSuspendProcess.return_value = 0
        native.NtResumeProcess.side_effect = [-1073741790, 0]
        processes = [dict(ProcessId=n, ExecutablePath='C:/synthetic/vendor.exe') for n in (1, 2)]
        with patch.object(vendor_pause, 'vendor_processes', return_value=processes), \
             patch.object(vendor_pause.ctypes, 'WinDLL', side_effect=[kernel, native]):
            with self.assertRaisesRegex(OSError, '恢复官方软件运行失败') as raised:
                with vendor_pause.pause_vendor():
                    raise OSError('USB unplugged during read')
        text = describe_error(raised.exception)
        self.assertIn('0xC0000022', text)
        self.assertIn('USB unplugged during read', text)
        self.assertEqual([c.args[0] for c in native.NtResumeProcess.call_args_list], [20, 10])
        self.assertEqual([c.args[0] for c in kernel.CloseHandle.call_args_list], [20, 10])


if __name__ == '__main__':
    unittest.main()
