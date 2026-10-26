"""Bounded, read-only snapshots of the boot container's first JPEG.

The currently displayed live USB frame is not read here. No full animation
readback, write/commit or playback-mode change is performed.
"""
import io
import struct
import time
from PIL import Image
from lg700_core import READ_CHUNK, MAX_JPEG, checksum_record, sha256


def read_boot_first_frame(device, progress=lambda fraction: None):
    started = time.monotonic()
    meta = device.metadata(1)
    transferred = 0

    def read_range(offset, length):
        nonlocal transferred
        if offset < 0 or length <= 0 or offset + length > meta['size']:
            raise ValueError('首帧读取范围超出硬件动画')
        result = bytearray()
        while len(result) < length:
            position = offset + len(result)
            count = min(READ_CHUNK, length - len(result))
            for attempt in range(2):
                try:
                    raw = device.query(0x0D, struct.pack('<IIi', position, count, 0))
                    if len(raw) < 12:
                        raise OSError('首帧读取响应太短')
                    actual_offset, actual_length, status = struct.unpack_from('<IIi', raw)
                    if (actual_offset != position or actual_length != count or
                            status < 0 or len(raw) < 12 + count):
                        raise OSError('首帧读取响应偏移、长度或状态不匹配')
                    break
                except (OSError, TimeoutError):
                    if attempt:
                        raise
                    device._recover_read_session(meta)
            result.extend(raw[12:12 + count])
            transferred += count
        return bytes(result)

    # Read only the root header and first directory record, never the whole
    # frame table. All offsets are validated before device reads are issued.
    header = read_range(0, 16)
    check, size, table_size, prefix, flags = struct.unpack('<IIIHH', header)
    frames = flags & 0xFFF
    if (check != checksum_record(size, table_size, prefix, flags) or
            flags & 0xF000 != 0x1000 or not frames or prefix != 1 or
            size != meta['size'] - 16 or table_size != frames * 16):
        raise ValueError('硬件动画包头或帧目录格式异常；没有读取整段动画')
    directory = read_range(16, prefix + 16)
    check, offset, length, name_length, frame_flags = struct.unpack_from('<IIIHH', directory, prefix)
    if (directory[0] != 0 or check != checksum_record(offset, length, name_length, frame_flags) or
            frame_flags != 0 or not 1 <= name_length <= 128 or not 4 <= length <= MAX_JPEG or
            offset < 16 + prefix + table_size or offset + name_length + length > size):
        raise ValueError('硬件首帧记录校验失败或偏移越界')
    progress(.15)
    payload = read_range(offset, name_length + length)
    if payload[name_length - 1] != 0:
        raise ValueError('硬件首帧文件名记录损坏')
    jpeg = payload[name_length:]
    if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
        raise ValueError('硬件首帧 JPEG 不完整')
    with Image.open(io.BytesIO(jpeg)) as image:
        if image.format != 'JPEG' or image.size != device.profile.size or image.info.get('progressive'):
            raise ValueError(f'硬件首帧必须是 {device.profile.dimensions} 非渐进 JPEG')
        image.verify()
    with Image.open(io.BytesIO(jpeg)) as image:
        image.load()
    progress(.6)
    # Firmware playback can race reads. Two equal small snapshots and stable
    # directory/metadata are required. Failure never falls back to vendor data.
    if read_range(offset, name_length + length) != payload:
        raise OSError('两次硬件首帧读取内容不一致，请稍后重新识别')
    if read_range(0, 16) != header or read_range(16, prefix + 16) != directory:
        raise OSError('首帧读取期间动画目录发生变化，请重新识别')
    current = device.metadata(1)
    identity = ('selector', 'size', 'active_record', 'next_record', 'extra')
    if any(current[key] != meta[key] for key in identity):
        raise OSError('首帧读取期间硬件素材记录发生变化，请重新识别')
    progress(1)
    return {'jpeg': jpeg, 'frame_count': frames, 'total_size': meta['size'],
            'frame_size': length, 'read_bytes': transferred, 'sha256': sha256(jpeg),
            'metadata': meta, 'seconds': time.monotonic() - started,
            'source': 'hardware_boot_first_frame'}
