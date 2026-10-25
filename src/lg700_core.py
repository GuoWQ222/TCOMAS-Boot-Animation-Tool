"""TCOMAS LG700 native boot-asset protocol, Windows HID.

Reverse engineered from the owner's device (REV 0067) and vendor V75.
This module never updates executable firmware. It exposes asset commands and
the vendor's 0x1E offline display-mode setting (only values 0 and 1).
"""
from __future__ import annotations
import hashlib
import io
import json
import logging
import math
import os
import struct
import time
from pathlib import Path

VID, PID = 0x1A2C, 0x151C
SECTOR = 65536
REGIONS = {1: (0x400000, 0xF00000), 0: (0xF00000, 0x1000000)}
SUPPORTED_REVISIONS = {0x67, 0x75}
CMD_PAYLOAD = 436
MAX_PACKETS = 50
MAX_JPEG = 200_000
READ_CHUNK = 8192
WRITE_CHUNK = 8192
log = logging.getLogger('lg700.transport')


class ReadResponseError(OSError):
    """A read-only response did not match the requested offset or length."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def checksum_record(a, b, c, flags=0):
    return (-(a + b + c + (flags & 0xFFF))) & 0xFFFFFFFF


def pack_boot(jpegs: list[bytes]) -> bytes:
    """Original 16-byte file records, ASCII names, baseline JPEG payloads."""
    if not 1 <= len(jpegs) <= 4095:
        raise ValueError("动画必须包含 1–4095 帧")
    offset = 17 + 16 * len(jpegs)
    table, body = bytearray(), bytearray()
    for n, jpeg in enumerate(jpegs):
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError(f"第 {n} 帧 JPEG 不完整")
        if len(jpeg) > MAX_JPEG:
            raise ValueError(f"第 {n} 帧超过 200 KB")
        name = f"{n}.jpg\0".encode("ascii")
        check = checksum_record(offset, len(jpeg), len(name))
        table += struct.pack("<IIIHH", check, offset, len(jpeg), len(name), 0)
        body += name + jpeg
        offset += len(name) + len(jpeg)
    # The final empty directory record is part of the original container.
    size, table_size, flags = offset, len(table), 0x1000 | len(jpegs)
    header = struct.pack("<IIIHH", checksum_record(size, table_size, 1, flags),
                         size, table_size, 1, flags)
    terminal = struct.pack("<IIIHH", 0, 0, 0, 0, 0x1000)
    return header + b"\0" + table + body + terminal


def unpack_boot(data: bytes, inspect_jpeg=True) -> list[bytes]:
    from PIL import Image
    if len(data) < 33:
        raise ValueError("动画包太短")
    check, size, table_size, prefix, flags = struct.unpack_from("<IIIHH", data)
    count = flags & 0xFFF
    if check != checksum_record(size, table_size, prefix, flags):
        raise ValueError("动画包头校验失败")
    if flags & 0xF000 != 0x1000 or not count:
        raise ValueError("不是 LG700 原厂动画格式")
    if size != len(data) - 16 or table_size != count * 16:
        raise ValueError("动画包长度或帧表错误")
    table_start = 16 + prefix
    table_end = table_start + table_size
    if table_end > len(data):
        raise ValueError("帧表越界")
    frames, previous_end = [], table_end
    for n in range(count):
        check, off, length, name_len, frame_flags = struct.unpack_from(
            "<IIIHH", data, table_start + 16 * n)
        if check != checksum_record(off, length, name_len, frame_flags):
            raise ValueError(f"第 {n} 帧记录校验失败")
        end = off + name_len + length
        if off < previous_end or not name_len or end > len(data):
            raise ValueError(f"第 {n} 帧偏移越界或重叠")
        if not data[off:off + name_len].endswith(b"\0"):
            raise ValueError(f"第 {n} 帧名称损坏")
        jpeg = data[off + name_len:end]
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError(f"第 {n} 帧 JPEG 损坏")
        if length > MAX_JPEG:
            raise ValueError(f"第 {n} 帧过大")
        if inspect_jpeg:
            with Image.open(io.BytesIO(jpeg)) as im:
                if im.size != (640, 480) or im.info.get("progressive"):
                    raise ValueError(f"第 {n} 帧必须是 640×480 非渐进 JPEG")
                im.verify()
        frames.append(jpeg)
        previous_end = end
    if data[previous_end:] != struct.pack("<IIIHH", 0, 0, 0, 0, 0x1000):
        raise ValueError("动画包结束记录损坏")
    return frames


def capacity(selector):
    begin, end = REGIONS[selector]
    return end - begin - 20  # storage record is outside the animation container


def validate_asset(selector, data):
    if selector not in REGIONS:
        raise ValueError("只允许开机动画和待机图区域")
    begin, end = REGIONS[selector]
    required = ((len(data) + 3) // 4 * 4 + 20 + SECTOR - 1) // SECTOR * SECTOR
    if not data or begin + required > end:
        raise ValueError(f"素材超过分区容量：{len(data):,} 字节")
    if selector == 1:
        unpack_boot(data)
    else:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "JPEG" or im.size != (640, 480) or im.info.get("progressive"):
                raise ValueError("待机图必须是 640×480 非渐进 JPEG")
            im.verify()


def encode_reports(op, payload=b""):
    count = max(1, math.ceil(len(payload) / CMD_PAYLOAD))
    if not 0 <= op <= 255 or count > MAX_PACKETS:
        raise ValueError("命令过大")
    for seq in range(count):
        head = (0x80000000 | count << 8 | op) if seq == 0 else (seq << 8 | op)
        chunk = payload[seq * CMD_PAYLOAD:(seq + 1) * CMD_PAYLOAD]
        yield b"\0" + struct.pack("<I", head) + chunk.ljust(CMD_PAYLOAD, b"\0")


class LG700:
    def __init__(self):
        import hid
        self.hid = None
        self._mutex = None
        self._kernel = None
        devices = [d for d in hid.enumerate(VID, PID)
                   if d.get("interface_number") == 0 and d.get("usage_page") == 0xFF06]
        if len(devices) != 1:
            raise RuntimeError(f"需要恰好一台 LG700，检测到 {len(devices)} 台")
        self.info = devices[0]
        try:
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                self._kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                self._kernel.CreateMutexW.restype = wintypes.HANDLE
                self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                self._kernel.WaitForSingleObject.restype = wintypes.DWORD
                self._kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
                self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = self._kernel.CreateMutexW(None, False, 'Local\\TCOMAS_LG700_Asset_IO')
                if not handle:
                    raise OSError('无法创建屏幕通信锁')
                status = self._kernel.WaitForSingleObject(handle, 0)
                if status not in (0, 0x80):
                    self._kernel.CloseHandle(handle)
                    raise RuntimeError('另一个写入工具正在使用屏幕，请等待它完成后重试')
                self._mutex = handle
            self.hid = hid.device()
            self.hid.open_path(self.info["path"])
            self.revision = struct.unpack_from("<H", self.query(0x87))[0]
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        try:
            if self.hid is not None:
                self.hid.close()
                self.hid = None
        finally:
            if self._mutex is not None:
                self._kernel.ReleaseMutex(self._mutex)
                self._kernel.CloseHandle(self._mutex)
                self._mutex = None

    def query(self, op, payload=b"", timeout_ms=6000):
        if op == 0x1E:
            if payload not in (b'\x00', b'\x01'):
                raise ValueError('离线播放模式只允许 0 或 1')
        elif op not in (0x0A, 0x0B, 0x0C, 0x0D, 0x87):
            raise ValueError("该工具不发送此类命令")
        for report in encode_reports(op, payload):
            if self.hid.write(report) != 441:
                raise OSError("HID 发送长度异常")
        deadline = time.monotonic() + timeout_ms / 1000
        last_unexpected = None
        while True:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            raw = bytes(self.hid.read(440, remaining))
            if len(raw) != 440:
                detail = f'；最后收到旧包头 0x{last_unexpected:08X}' if last_unexpected is not None else ''
                raise TimeoutError(f"命令 0x{op:02X} 等待响应超时{detail}")
            head = struct.unpack_from("<I", raw)[0]
            if head & 0xFF == op and head & 0x80000000:
                break
            last_unexpected = head
            log.warning('Discarded unrelated packet while waiting for op=%02X: header=%08X', op, head)
            if time.monotonic() >= deadline:
                raise TimeoutError("收到其他命令响应，请退出原厂软件后重试")
        count = (head & 0x7FFFFFFF) >> 8
        if not 1 <= count <= MAX_PACKETS:
            raise OSError("设备返回无效包数")
        chunks = [raw[4:]]
        for seq in range(1, count):
            raw = bytes(self.hid.read(440, timeout_ms))
            if len(raw) != 440 or struct.unpack_from("<I", raw)[0] != (seq << 8 | op):
                raise OSError(f"响应包顺序错误：{seq}/{count}")
            chunks.append(raw[4:])
        result = b"".join(chunks)
        if op == 0x0D:
            offset, requested, _ = struct.unpack_from('<IIi', payload)
            received_offset, actual, status = struct.unpack_from('<IIi', result)
            expected_packets = math.ceil((12 + requested) / CMD_PAYLOAD)
            if (received_offset != offset or actual != requested or status < 0 or
                    count != expected_packets or len(result) < 12 + requested):
                message = (f'读取响应不匹配：请求 offset={offset}, length={requested}；'
                           f'返回 offset={received_offset}, length={actual}, status={status}, '
                           f'packets={count}/{expected_packets}')
                log.error(message)
                raise ReadResponseError(message)
        return result

    def _recover_read_session(self, original_meta):
        """Discard leftover input, then reselect the same unchanged asset.

        Only used for failed reads. It never retries write/commit/mode commands.
        """
        discarded = 0
        for _ in range(MAX_PACKETS * 2):
            raw = bytes(self.hid.read(440, 10))
            if not raw:
                break
            discarded += 1
        else:
            raise OSError('命令口持续返回旧数据，已停止；请让屏幕彻底断电后重试')
        current = self.metadata(original_meta['selector'])
        if self._asset_identity(current) != self._asset_identity(original_meta):
            raise OSError('读取期间屏幕素材发生变化，已停止，不能混合保存新旧数据')
        log.warning('Read-only recovery: discarded=%d, unchanged selector=%d', discarded, current['selector'])

    @staticmethod
    def _asset_identity(meta):
        return tuple(meta[k] for k in ('selector', 'size', 'active_record', 'next_record'))

    def _reconnect_command_interface(self, timeout=10):
        """Only retry read-only probes; never resend the mode-setting command."""
        import hid
        expected_serial = self.info.get('serial_number')
        expected_revision = self.revision
        if self.hid is not None:
            try:
                self.hid.close()
            except OSError:
                pass
            self.hid = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [d for d in hid.enumerate(VID, PID)
                       if d.get('interface_number') == 0 and d.get('usage_page') == 0xFF06
                       and d.get('serial_number') == expected_serial]
            if len(matches) == 1:
                try:
                    self.hid = hid.device()
                    self.hid.open_path(matches[0]['path'])
                    revision = struct.unpack_from('<H', self.query(0x87, timeout_ms=400))[0]
                    if revision != expected_revision:
                        raise RuntimeError('重新连接后固件版本变化，已停止')
                    self.info = matches[0]
                    return
                except (OSError, TimeoutError):
                    if self.hid is not None:
                        self.hid.close()
                        self.hid = None
            time.sleep(.25)
        raise TimeoutError('屏幕尚未重新连接；未重复发送模式命令')

    def set_offline_loop(self, enabled, backup_directory):
        """Set native looping; a one-frame boot package stays visually static.

        V75: 0x1E writes config[0x111], starts the boot task for mode 1,
        and schedules configuration persistence. The protocol has no getter
        for this byte: an ACK proves command acceptance, not visible playback.
        """
        if self.revision not in SUPPORTED_REVISIONS:
            raise RuntimeError(f'固件 REV {self.revision:04X} 尚未验证离线播放接口')
        directory = Path(backup_directory)
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
        if manifest['device'].get('revision') != f'{self.revision:04X}':
            raise ValueError('播放模式备份固件版本不匹配')
        for name in ('boot', 'standby'):
            saved = manifest['assets'][name]
            if sha256((directory / saved['file']).read_bytes()) != saved['sha256']:
                raise ValueError('设置播放模式前，素材备份 SHA-256 校验失败')
        audit = {'revision': f'{self.revision:04X}', 'command': '0x1E',
                 'mode': 1 if enabled else 0, 'previous_mode': None,
                 'mode_readback_supported': False, 'acknowledged': False,
                 'device_reconnected': False,
                 'visual_playback_verified': False, 'time_unix': time.time()}
        audit_path = directory / 'playback_mode.json'
        audit_path.write_text(json.dumps(audit, indent=2), encoding='utf8')
        try:
            self.query(0x1E, bytes([audit['mode']]))
        except (OSError, TimeoutError) as error:
            audit['error'] = f'{type(error).__name__}: {error}'
            audit['result'] = 'unknown_after_send_do_not_retry_automatically'
            audit_path.write_text(json.dumps(audit, indent=2), encoding='utf8')
            # REV 0067 was physically verified to restart its USB connection
            # after switching to mode 1, and then loop the saved video.
            try:
                self._reconnect_command_interface()
            except Exception as reconnect_error:
                audit['reconnect_error'] = str(reconnect_error)
                audit_path.write_text(json.dumps(audit, indent=2), encoding='utf8')
                raise RuntimeError('离线播放命令后屏幕未重新连接；素材备份已保留，'
                                   '没有重复发送命令。') from reconnect_error
            audit['device_reconnected'] = True
            audit['result'] = 'sent_then_reconnected_mode_byte_unreadable'
        else:
            audit['acknowledged'] = True
            audit['result'] = 'command_acknowledged_mode_byte_unreadable'
        # Firmware defers saving configuration; don't close immediately.
        time.sleep(1)
        audit_path.write_text(json.dumps(audit, indent=2), encoding='utf8')
        return audit

    def metadata(self, selector):
        raw = self.query(0x0C, struct.pack("<II", selector, 0))
        sel, size, active, next_record, extra = struct.unpack_from("<IiIII", raw)
        if sel != selector or not 0 < size <= capacity(selector):
            raise OSError(f"素材元数据异常：selector={sel}, size={size}")
        begin, end = REGIONS[selector]
        if active < begin or active >= end or not active < next_record <= end:
            raise OSError("素材地址与已知 LG700 存储布局不符，已停止")
        return dict(selector=sel, size=size, active_record=active,
                    next_record=next_record, extra=extra)

    def read_asset(self, selector, progress=lambda p: None):
        meta = self.metadata(selector)
        log.info('Read start selector=%d size=%d chunk=%d', selector, meta['size'], READ_CHUNK)
        result = bytearray()
        while len(result) < meta["size"]:
            offset, count = len(result), min(READ_CHUNK, meta["size"] - len(result))
            for attempt in range(2):
                try:
                    raw = self.query(0x0D, struct.pack("<IIi", offset, count, 0))
                    off, actual, status = struct.unpack_from("<IIi", raw)
                    if off != offset or actual != count or status < 0 or len(raw) < 12 + count:
                        raise ReadResponseError(f'读取响应不匹配：请求 {offset}/{count}，返回 {off}/{actual}/{status}')
                    break
                except (OSError, TimeoutError) as error:
                    log.warning('Read failure selector=%d offset=%d attempt=%d: %s', selector, offset, attempt + 1, error)
                    if attempt:
                        raise OSError(f'读取在 offset={offset} 失败，已停止本次操作：{error}') from error
                    self._recover_read_session(meta)
            result.extend(raw[12:12 + count])
            progress(len(result) / meta["size"])
        if self._asset_identity(self.metadata(selector)) != self._asset_identity(meta):
            raise OSError('读取结束时素材元数据已改变，已拒绝保存本次读取结果')
        log.info('Read complete selector=%d bytes=%d sha256=%s', selector, len(result), sha256(result))
        return bytes(result), meta

    def backup(self, directory, progress=lambda s, p: None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {"device": {"vid": "1A2C", "pid": "151C",
                    "revision": f"{self.revision:04X}"}, "assets": {}}
        for selector, name in [(1, "boot"), (0, "standby")]:
            data, meta = self.read_asset(selector, lambda p: progress(name, p))
            validate_asset(selector, data)
            file = directory / f"{name}.bin"
            file.write_bytes(data)
            # Verify the persisted backup before exposing it to the write operation.
            if file.read_bytes() != data:
                raise OSError("备份文件写入校验失败")
            meta.update(file=file.name, sha256=sha256(data))
            manifest["assets"][name] = meta
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
        return manifest

    def write_asset(self, selector, data, backup_directory, progress=lambda p: None,
                    recovery=False):
        """Only call after backup(); commit is sent exactly once, no blind retries."""
        self.last_write_skipped = False
        self.last_verification_reconnected = False
        validate_asset(selector, data)
        if self.revision not in SUPPORTED_REVISIONS:
            raise RuntimeError(f"写入尚未验证的固件 REV {self.revision:04X}，已停止")
        directory = Path(backup_directory)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
        if manifest["device"].get("revision") != f"{self.revision:04X}":
            raise ValueError("备份固件版本不匹配")
        asset_name = "boot" if selector else "standby"
        saved = manifest["assets"][asset_name]
        previous = (directory / saved["file"]).read_bytes()
        if sha256(previous) != saved["sha256"]:
            raise ValueError("备份 SHA-256 校验失败")
        if not recovery:
            current_meta = self.metadata(selector)
            if current_meta["size"] != saved["size"]:
                raise ValueError("备份后素材发生变化，请重新备份")
            if previous == data:
                # The backup suggests no change. Still read the entire live
                # asset again: size/cursors alone cannot prove byte equality.
                actual = self._read_for_verification(selector, progress)
                if actual != data:
                    raise ValueError('备份后素材内容发生变化，请重新备份；尚未开始擦写')
                self.last_write_skipped = True
                return sha256(actual)
        ack = self.query(0x0A, struct.pack("<II", selector, len(data)), timeout_ms=15000)
        which, accepted = struct.unpack_from("<Ii", ack)
        if which != selector or accepted != len(data):
            raise OSError("设备拒绝开始写入，未发送素材")
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + WRITE_CHUNK]
            final = offset + len(chunk) == len(data)
            # Byte 8 is progress percentage; >=100 commits the storage record.
            percent = 100 if final else min(99, (offset + len(chunk)) * 100 // len(data))
            payload = struct.pack("<III", offset, len(chunk), percent) + chunk
            ack = self.query(0x0B, payload, timeout_ms=15000)
            off, length, status = struct.unpack_from("<IIi", ack)
            if off != offset or length != len(chunk) or status < 0:
                raise OSError(f"写入失败：offset={offset}, status={status}。保留备份，可用恢复功能。")
            offset += len(chunk)
            progress(offset / len(data))
        actual = self._read_for_verification(selector, progress)
        if actual != data:
            raise OSError("写后读回内容不一致，请使用备份恢复")
        return sha256(actual)

    def _read_for_verification(self, selector, progress):
        try:
            data, _ = self.read_asset(selector, lambda p: progress(1 + p))
        except (OSError, TimeoutError):
            # Native loop mode can reset USB just after a committed write.
            # Reconnect and restart the complete read; never replay writes.
            self._reconnect_command_interface()
            self.last_verification_reconnected = True
            data, _ = self.read_asset(selector, lambda p: progress(1 + p))
        return data


def restore_file(directory, selector):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    name = "boot" if selector else "standby"
    record = manifest["assets"][name]
    data = (directory / record["file"]).read_bytes()
    if sha256(data) != record["sha256"]:
        raise ValueError("恢复文件 SHA-256 校验失败")
    validate_asset(selector, data)
    return data
