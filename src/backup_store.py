"""Per-user, per-device backups, independent of install folder and drive letter."""
import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from lg700_core import sha256, restore_file


def data_home():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    return root / "TCOMAS-LG700-Tool"


def device_home(device):
    serial = device.info.get("serial_number") or device.info.get("path", b"").decode(errors="replace")
    identity = str(device.info.get("product_string", "LG700")) + "|" + serial
    key = sha256(identity.encode("utf8"))[:16]
    return data_home() / "Backups" / key


def new_snapshot(device, suffix):
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    return device_home(device) / (stamp + "_" + suffix)


def preserve_original(device, snapshot):
    original = device_home(device) / "Original"
    if original.exists():
        # Never replace the first valid original with later custom animations.
        restore_file(original, 1)
        restore_file(original, 0)
        return original
    original.parent.mkdir(parents=True, exist_ok=True)
    temporary = original.with_name("Original.pending")
    if temporary.exists():
        raise RuntimeError("存在未完成的原始备份，请先检查 Original.pending 目录")
    shutil.copytree(snapshot, temporary)
    restore_file(temporary, 1)
    restore_file(temporary, 0)
    temporary.rename(original)
    return original


def original_directory(device):
    directory = device_home(device) / "Original"
    if not (directory / "manifest.json").is_file():
        raise FileNotFoundError("此电脑尚无该屏幕的原始备份，请先备份或导入原始备份目录")
    restore_file(directory, 1)
    restore_file(directory, 0)
    return directory
