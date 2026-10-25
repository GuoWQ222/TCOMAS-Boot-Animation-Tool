"""Temporarily suspend only the T'OWEI main process and always resume it.

No settings are changed; the GUI and native frame sender pause together.
"""
import contextlib
import ctypes
import json
import subprocess
import sys
from ctypes import wintypes


def vendor_processes():
    if sys.platform != "win32":
        return []
    command = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
               "Get-CimInstance Win32_Process | Where-Object {"
               "$_.Name -eq \"T'OWEI.exe\" -and $_.CommandLine -notmatch '--type='"
               "} | Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, encoding="utf8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    if result.returncode:
        raise OSError("无法查询原厂软件进程，请手动退出后重试")
    processes = json.loads(result.stdout) if result.stdout.strip() else []
    return [processes] if isinstance(processes, dict) else processes


@contextlib.contextmanager
def pause_vendor():
    if sys.platform != "win32":
        yield
        return
    processes = vendor_processes()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    native.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    native.NtSuspendProcess.restype = wintypes.LONG
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = wintypes.LONG
    handles = []
    try:
        for process in processes:
            handle = kernel.OpenProcess(0x0800 | 0x1000, False, process["ProcessId"])
            if not handle:
                raise OSError("无法临时暂停钛坦软件，请手动退出它后重试")
            path, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            ok = kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length))
            if not ok or path.value.lower() != process["ExecutablePath"].lower():
                kernel.CloseHandle(handle)
                raise OSError("钛坦进程路径变化，已停止")
            status = native.NtSuspendProcess(handle)
            if status != 0:
                kernel.CloseHandle(handle)
                raise OSError(f"暂停传屏失败：NTSTATUS={status}")
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            native.NtResumeProcess(handle)
            kernel.CloseHandle(handle)
