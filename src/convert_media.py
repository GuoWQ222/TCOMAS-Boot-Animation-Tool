"""Local conversion to the LG700's native JPEG-frame container."""
import shutil
import subprocess
import tempfile
import os
import threading
from collections import OrderedDict
from pathlib import Path
from lg700_core import pack_boot, unpack_boot, validate_asset

_conversion_cache = OrderedDict()
_conversion_lock = threading.Lock()


def cached_build_asset(source, quality=75, report=lambda s: None, allow_oversize=False):
    """Share exact converted bytes between quality preview and hardware writing."""
    source = Path(source)
    quality = max(35, min(90, int(quality)))
    def signature():
        stat = source.stat()
        return str(source.resolve()), stat.st_mtime_ns, stat.st_size, quality
    with _conversion_lock:
        key = signature()
        if key in _conversion_cache:
            data = _conversion_cache.pop(key)
            report('复用已完成的相同画质转换，动画数据不变。')
        else:
            data = build_asset(source, quality, report, allow_oversize=True)
        if signature() != key:
            raise ValueError('转换期间源文件发生变化，请重新选择素材')
        _conversion_cache[key] = data
        while len(_conversion_cache) > 3:
            _conversion_cache.popitem(last=False)
    if not allow_oversize:
        validate_asset(1, data)
    return data


def find_ffmpeg():
    from vendor_pause import vendor_processes
    import sys
    candidates = [shutil.which("ffmpeg"),
        str(Path(sys.executable).parent / "ffmpeg.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/ffmpeg/bin/ffmpeg.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    for process in vendor_processes():
        candidate = Path(process['ExecutablePath']).parent / "resources/app.asar.unpacked/bin/ffmpeg.exe"
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("未找到 FFmpeg。请将 ffmpeg.exe 放在程序目录，或安装到 PATH。")


def build_asset(source, quality=75, report=lambda s: None, allow_oversize=False):
    """Encode once; preview may measure an oversized package without writing it."""
    source = Path(source)
    quality = max(35, min(90, int(quality)))
    if source.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        from PIL import Image, ImageOps
        import io
        with Image.open(source) as im:
            im = ImageOps.contain(im.convert("RGB"), (640, 480))
            canvas = Image.new("RGB", (640, 480))
            canvas.paste(im, ((640 - im.width) // 2, (480 - im.height) // 2))
            buffer = io.BytesIO()
            canvas.save(buffer, "JPEG", quality=quality, progressive=False, subsampling=2)
            frames = [buffer.getvalue()]
    else:
        import sys
        near = Path(sys.executable).parent / "ffmpeg.exe"
        ffmpeg = str(near) if near.exists() else find_ffmpeg()
        report("正在转换为 640×480 JPEG 序列……")
        with tempfile.TemporaryDirectory(prefix="lg700_convert_") as folder:
            output = str(Path(folder) / "frame_%05d.jpg")
            args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source),
                    "-vf", "fps=30,scale=640:480:force_original_aspect_ratio=decrease:flags=lanczos,"
                           "pad=640:480:(ow-iw)/2:(oh-ih)/2",
                    "-an", "-frames:v", "4096", "-q:v", str(max(2, (100 - quality) // 5)),
                    "-pix_fmt", "yuvj420p", output]
            result = subprocess.run(args, capture_output=True, text=True,
                                    encoding="utf8", errors="replace",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError("视频转换失败：" + result.stderr[-1000:])
            files = sorted(Path(folder).glob("frame_*.jpg"))
            if len(files) > 4095:
                raise ValueError("视频过长；最多支持 4095 帧，请先裁剪视频")
            frames = [p.read_bytes() for p in files]
    report(f"检查 {len(frames)} 帧及存储容量……")
    data = pack_boot(frames)
    if allow_oversize:
        unpack_boot(data)
    else:
        validate_asset(1, data)
    validate_asset(0, frames[0])
    return data


def convert(source, destination, quality=75, report=lambda s: None):
    destination = Path(destination)
    data = cached_build_asset(source, quality, report)
    frames = unpack_boot(data, inspect_jpeg=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    destination.with_suffix(".jpg").write_bytes(frames[0])
    return data
