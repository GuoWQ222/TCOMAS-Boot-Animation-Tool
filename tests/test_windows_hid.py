"""Offline checks for cancellation, completion, and report boundaries."""
import ctypes
import unittest
from unittest.mock import Mock, patch
import windows_hid as native


class NativeHIDTests(unittest.TestCase):
    def device(self):
        device = native.WindowsHID()
        device.handle = 100
        device.read_event = 101
        device.write_event = 102
        return device

    def test_timed_out_read_is_cancelled_and_joined_before_return(self):
        kernel = Mock()
        kernel.ReadFile.return_value = False
        kernel.WaitForSingleObject.return_value = 258
        with patch.object(native, 'kernel', kernel), patch.object(native.ctypes, 'get_last_error', return_value=997):
            self.assertEqual(self.device().read(440, 10), b'')
        names = [call[0] for call in kernel.mock_calls]
        self.assertLess(names.index('CancelIoEx'), names.index('GetOverlappedResult'))
        self.assertIs(kernel.GetOverlappedResult.call_args.args[-1], True)

    def test_timed_out_output_is_not_replayed(self):
        kernel = Mock()
        kernel.WriteFile.return_value = False
        kernel.WaitForSingleObject.return_value = 258
        with patch.object(native, 'kernel', kernel), patch.object(native.ctypes, 'get_last_error', return_value=997):
            with self.assertRaises(TimeoutError):
                self.device().write(b'\0' * 441)
        kernel.WriteFile.assert_called_once()
        kernel.CancelIoEx.assert_called_once()
        self.assertIs(kernel.GetOverlappedResult.call_args.args[-1], True)

    def test_input_preserves_all_payload_bytes_and_removes_only_report_id(self):
        kernel = Mock()
        payload = bytes(range(220)) * 2
        def complete(handle, buffer, length, actual, overlapped):
            self.assertEqual(length, 441)
            ctypes.memmove(buffer, b'\0' + payload, 441)
            actual._obj.value = 441
            return True
        kernel.ReadFile.side_effect = complete
        with patch.object(native, 'kernel', kernel):
            self.assertEqual(self.device().read(440, 100), payload)
        kernel.CancelIoEx.assert_not_called()

    def test_output_preserves_the_original_report_and_rejects_wrong_lengths(self):
        kernel = Mock()
        payload = b'\0' + bytes(range(220)) * 2
        def complete(handle, buffer, length, actual, overlapped):
            self.assertEqual(bytes(buffer), payload)
            actual._obj.value = length
            return True
        kernel.WriteFile.side_effect = complete
        with patch.object(native, 'kernel', kernel):
            self.assertEqual(self.device().write(payload), 441)
            with self.assertRaises(ValueError):
                self.device().write(payload[:-1])
        kernel.WriteFile.assert_called_once()


if __name__ == '__main__':
    unittest.main()
