"""Read the active T'OWEI LG700 media selection without opening its database.

Understands Chromium localStorage LevelDB logs and Snappy SSTables. Pure Python;
the database is read only and is never locked, repaired, or modified.
"""
import json
import os
import struct
from pathlib import Path


def varint(data, offset):
    value = shift = 0
    while offset < len(data) and shift <= 63:
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
    raise ValueError("LevelDB varint 不完整")


def snappy(data):
    expected, p = varint(data, 0)
    if expected > 64 * 1024 * 1024:
        raise ValueError("LevelDB 块过大")
    out = bytearray()
    while p < len(data):
        tag = data[p]
        p += 1
        kind, length = tag & 3, tag >> 2
        if kind == 0:
            if length < 60:
                length += 1
            else:
                extra = length - 59
                length = int.from_bytes(data[p:p + extra], "little") + 1
                p += extra
            if p + length > len(data):
                raise ValueError("Snappy literal 越界")
            out.extend(data[p:p + length])
            p += length
        else:
            if kind == 1:
                length, distance = 4 + (length & 7), ((tag & 0xE0) << 3) | data[p]
                p += 1
            else:
                extra = 2 if kind == 2 else 4
                distance = int.from_bytes(data[p:p + extra], "little")
                p += extra
                length += 1
            if not 0 < distance <= len(out):
                raise ValueError("Snappy copy 越界")
            while length:
                chunk = out[-distance:][:length]
                out.extend(chunk)
                length -= len(chunk)
        if len(out) > expected:
            raise ValueError("Snappy 输出越界")
    if len(out) != expected:
        raise ValueError("Snappy 长度错误")
    return bytes(out)


def block_entries(data):
    if len(data) < 4:
        return
    restarts = struct.unpack_from("<I", data, len(data) - 4)[0]
    end = len(data) - 4 - 4 * restarts
    if end < 0:
        raise ValueError("LevelDB restart 数组损坏")
    p, previous = 0, b""
    while p < end:
        shared, p = varint(data, p)
        unshared, p = varint(data, p)
        size, p = varint(data, p)
        if shared > len(previous) or p + unshared + size > end:
            raise ValueError("LevelDB entry 越界")
        key = previous[:shared] + data[p:p + unshared]
        p += unshared
        value = data[p:p + size]
        p += size
        previous = key
        yield key, value


def sstable(data):
    if len(data) < 48 or data[-8:] != bytes.fromhex("57fb808b247547db"):
        return
    footer = len(data) - 48
    _, p = varint(data, footer)
    _, p = varint(data, p)
    index_offset, p = varint(data, p)
    index_size, _ = varint(data, p)
    def read_block(offset, size):
        if offset + size + 5 > len(data):
            raise ValueError("LevelDB block 越界")
        block = data[offset:offset + size]
        compression = data[offset + size]
        if compression == 1:
            return snappy(block)
        if compression != 0:
            raise ValueError("不支持的 LevelDB 压缩类型")
        return block
    for _, handle in block_entries(read_block(index_offset, index_size)):
        offset, p = varint(handle, 0)
        size, _ = varint(handle, p)
        for key, value in block_entries(read_block(offset, size)):
            if len(key) < 8:
                continue
            tag = struct.unpack_from("<Q", key, len(key) - 8)[0]
            yield key[:-8], tag >> 8, tag & 255, value


def log_records(data):
    fragments = []
    for start in range(0, len(data), 32768):
        p, end = start, min(start + 32768, len(data))
        while p + 7 <= end:
            _, size, kind = struct.unpack_from("<IHB", data, p)
            p += 7
            if size == kind == 0:
                break
            if p + size > end:
                break  # a live log may end with an incomplete write
            payload = data[p:p + size]
            p += size
            if kind == 1:
                yield payload
            elif kind == 2:
                fragments = [payload]
            elif kind == 3:
                fragments.append(payload)
            elif kind == 4:
                fragments.append(payload)
                yield b"".join(fragments)
                fragments = []


def write_batches(data):
    for record in log_records(data):
        if len(record) < 12:
            continue
        sequence, count = struct.unpack_from("<QI", record)
        p = 12
        for i in range(count):
            kind, p = record[p], p + 1
            size, p = varint(record, p)
            key, p = record[p:p + size], p + size
            value = b""
            if kind == 1:
                size, p = varint(record, p)
                value, p = record[p:p + size], p + size
            yield key, sequence + i, kind, value


def local_storage(directory, key_name):
    latest = None
    for file in list(directory.glob("*.ldb")) + list(directory.glob("*.log")):
        try:
            raw = file.read_bytes()
            entries = sstable(raw) if file.suffix == ".ldb" else write_batches(raw)
            for key, sequence, kind, value in entries:
                if key.endswith(b"\0\1" + key_name.encode()) and (latest is None or sequence > latest[0]):
                    latest = (sequence, kind, value)
        except FileNotFoundError:
            continue  # Chromium may compact an SSTable while being read
    if latest is None or latest[1] != 1:
        raise FileNotFoundError("未找到 LG700 当前页面记录，请先在钛坦软件中应用动画")
    value = latest[2]
    if not value:
        raise ValueError("当前动画记录为空")
    encoding = "latin1" if value[0] == 1 else "utf-16-le"
    return json.loads(value[1:].decode(encoding))


def detect_current_media():
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    problems = []
    for name in ("T'OWEI", "Tcomas"):
        home = appdata / name
        directory = home / "Local Storage/leveldb"
        if not directory.exists():
            continue
        try:
            state = local_storage(directory, "lg700_lastPageState")
            data = state.get("data", {})
            preset = data.get("currentPreset", {})
            if not isinstance(preset, dict):
                raise ValueError("当前页面不是可写入的图片或视频")
            if data.get("selectedSensors"):
                raise ValueError("当前画面包含实时监控数据；请先在钛坦软件中选择纯视频/图片")
            candidates = [preset.get(k) for k in ("nativePath", "video", "image")]
            for candidate in candidates:
                if not isinstance(candidate, str) or candidate.startswith("blob:"):
                    continue
                candidate = candidate.removeprefix("file:///")
                path = Path(candidate)
                if not path.is_absolute():
                    relative = candidate.removeprefix("./").removeprefix("led/")
                    path = home / "led-assets" / relative
                if path.is_file():
                    return {"path": str(path), "name": path.name,
                            "page": state.get("pageType"), "bytes": path.stat().st_size,
                            "source": "T'OWEI 当前选择", "state": state}
            raise FileNotFoundError("当前动画的本地文件不存在，请在钛坦软件中重新应用素材")
        except (ValueError, OSError, KeyError, struct.error) as error:
            problems.append(str(error))
    raise FileNotFoundError("；".join(problems) or "未发现 T'OWEI 的 LG700 动画配置")
