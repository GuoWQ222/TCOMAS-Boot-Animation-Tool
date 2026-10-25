"""Windows HID transport; no device firmware or settings changes.

Uses the same report bytes as hidapi. Each read has its own overlapped request;
timed-out reads are cancelled and joined before returning. Input buffering is
configured on this host handle only.
"""
import ctypes
from ctypes import wintypes
import logging

log = logging.getLogger('lg700.native_hid')
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
hid_dll = ctypes.WinDLL('hid', use_last_error=True)


class OVERLAPPED(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]


kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel.CreateEventW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.ResetEvent.argtypes = [wintypes.HANDLE]
kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
kernel.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
hid_dll.HidD_SetNumInputBuffers.argtypes = [wintypes.HANDLE, wintypes.ULONG]
hid_dll.HidD_SetNumInputBuffers.restype = wintypes.BOOLEAN
hid_dll.HidD_GetNumInputBuffers.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
hid_dll.HidD_GetNumInputBuffers.restype = wintypes.BOOLEAN
hid_dll.HidD_FlushQueue.argtypes = [wintypes.HANDLE]
hid_dll.HidD_FlushQueue.restype = wintypes.BOOLEAN


class WindowsHID:
    def __init__(self):
        self.handle = None
        self.read_event = None
        self.write_event = None
        self.report_length = 441

    def open_path(self, path):
        if isinstance(path, bytes):
            path = path.decode('utf8')
        self.handle = kernel.CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.read_event = kernel.CreateEventW(None, True, False, None)
            self.write_event = kernel.CreateEventW(None, True, False, None)
            if not self.read_event or not self.write_event:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = wintypes.ULONG()
            if not hid_dll.HidD_GetNumInputBuffers(self.handle, ctypes.byref(previous)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not hid_dll.HidD_SetNumInputBuffers(self.handle, 512):
                raise ctypes.WinError(ctypes.get_last_error())
            actual = wintypes.ULONG()
            if not hid_dll.HidD_GetNumInputBuffers(self.handle, ctypes.byref(actual)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not hid_dll.HidD_FlushQueue(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            log.info('Native handle open: input buffers %d -> %d; host input queue flushed', previous.value, actual.value)
        except Exception:
            self.close()
            raise

    def _transfer(self, writing, content_or_length, timeout):
        event = self.write_event if writing else self.read_event
        kernel.ResetEvent(event)
        overlapped = OVERLAPPED()
        overlapped.hEvent = event
        if writing:
            data = bytes(content_or_length)
            buffer = ctypes.create_string_buffer(data, len(data))
        else:
            buffer = ctypes.create_string_buffer(content_or_length)
        actual = wintypes.DWORD()
        method = kernel.WriteFile if writing else kernel.ReadFile
        success = method(self.handle, buffer, len(buffer), ctypes.byref(actual), ctypes.byref(overlapped))
        if not success:
            error = ctypes.get_last_error()
            if error != 997:
                raise ctypes.WinError(error)
            wait_result = kernel.WaitForSingleObject(event, timeout)
            if wait_result != 0:
                kernel.CancelIoEx(self.handle, ctypes.byref(overlapped))
                # Complete/cancel the I/O before releasing its buffer or OVERLAPPED.
                kernel.GetOverlappedResult(self.handle, ctypes.byref(overlapped), ctypes.byref(actual), True)
                if wait_result == 258 and not writing:
                    return b''
                if wait_result == 258:
                    raise TimeoutError('Windows HID output timed out')
                raise OSError('Windows HID wait failed: ' + str(wait_result))
            if not kernel.GetOverlappedResult(self.handle, ctypes.byref(overlapped), ctypes.byref(actual), False):
                raise ctypes.WinError(ctypes.get_last_error())
        return actual.value if writing else buffer.raw[:actual.value]

    def write(self, data):
        if len(data) != 441:
            raise ValueError('Only the known LG700 441-byte output report is allowed')
        return self._transfer(True, data, 6000)

    def read(self, length, timeout_ms):
        if length != 440:
            raise ValueError('Only the known LG700 440-byte input payload is allowed')
        data = self._transfer(False, self.report_length, max(1, timeout_ms))
        if data and data[0] != 0:
            raise OSError('Unexpected LG700 report ID')
        return data[1:] if data else b''

    def close(self):
        if self.handle:
            kernel.CancelIoEx(self.handle, None)
            kernel.CloseHandle(self.handle)
            self.handle = None
        for name in ('read_event', 'write_event'):
            handle = getattr(self, name)
            if handle:
                kernel.CloseHandle(handle)
                setattr(self, name, None)


