"""Per-user, per-device backups, independent of install folder and drive letter."""
import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from lg700_core import sha256, restore_file
from device_profiles import LG700_PROFILE, check_manifest_device

FACTORY_IMPORT = 'factory_asset_import'


def data_home():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    return root / "TCOMAS-LG700-Tool"


def device_home(device):
    path = device.info.get('path', b'')
    if isinstance(path, bytes):
        path = path.decode(errors='replace')
    serial = device.info.get("serial_number") or path
    identity = str(device.info.get("product_string", "LG700")) + "|" + serial
    key = sha256(identity.encode("utf8"))[:16]
    directory = data_home() / "Backups"
    # Preserve existing LG700 Original backups at their historical paths.
    if device.profile != LG700_PROFILE:
        directory /= device.profile.name
    return directory / key


def new_snapshot(device, suffix):
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    return device_home(device) / (stamp + "_" + suffix)


def preserve_original(device, snapshot):
    original = device_home(device) / "Original"
    if original.exists():
        # Never replace the first valid original with later custom animations.
        restore_file(original, 1, profile=device.profile, allow_empty=True)
        restore_file(original, 0, profile=device.profile)
        return original
    original.parent.mkdir(parents=True, exist_ok=True)
    temporary = original.with_name("Original.pending")
    if temporary.exists():
        raise RuntimeError("存在未完成的原始备份，请先检查 Original.pending 目录")
    shutil.copytree(snapshot, temporary)
    restore_file(temporary, 1, profile=device.profile, allow_empty=True)
    restore_file(temporary, 0, profile=device.profile)
    temporary.rename(original)
    return original


def original_directory(device):
    directory = device_home(device) / "Original"
    if not (directory / "manifest.json").is_file():
        raise FileNotFoundError("此电脑尚无该屏幕的原始备份，请先备份或导入原始备份目录")
    return directory


def restore_manifest(directory, device, *, allow_import=False):
    manifest = json.loads((Path(directory) / 'manifest.json').read_text(encoding='utf8'))
    check_manifest_device(manifest, device.profile, revision=device.revision, info=device.info)
    if manifest.get('kind') == FACTORY_IMPORT:
        if not allow_import:
            raise ValueError('导入包需要手动选择')
        source = manifest.get('source_backup', {}).get('device', {})
        target = manifest['device']
        if (device.profile != LG700_PROFILE or device.revision != 0x75 or
                not target.get('device_key') or source.get('revision') != '0067' or
                any(source.get(key) != target.get(key) for key in ('vid', 'pid')) or
                manifest.get('device_role') != 'import_target_not_capture' or
                any(manifest['assets'][name].get('file') != f'{name}.bin' for name in ('boot', 'standby'))):
            raise ValueError('导入包必须是绑定当前 LG700 的 0067 → 0075 原厂素材包')
    return manifest


def convert_original_assets(directory, device):
    """Prepare the compatible REV 0067 assets for a bound LG700 REV 0075 write."""
    manifest = json.loads((Path(directory) / 'manifest.json').read_text(encoding='utf8'))
    if manifest.get('kind') == FACTORY_IMPORT:
        raise ValueError('该目录是导入包，请通过备份目录选择窗口手动导入')
    check_manifest_device(manifest, device.profile, info=device.info)
    source = manifest['device']
    if (device.profile != LG700_PROFILE or device.revision != 0x75
            or source.get('revision') != '0067'
            or str(source.get('vid', '')).upper() != f'{LG700_PROFILE.vid:04X}'
            or str(source.get('pid', '')).upper() != f'{LG700_PROFILE.pid:04X}'):
        raise ValueError('目前仅支持同一台 LG700 的 REV 0067 → REV 0075 兼容转换')
    # REV 0075 accepts the original container and JPEG bytes. Validate both
    # assets for the target profile without rewriting the source backup.
    return {s: restore_file(directory, s, profile=device.profile) for s in (1, 0)}
