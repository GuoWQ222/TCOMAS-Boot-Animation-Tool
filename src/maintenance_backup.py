"""Pause native playback before update backups when a verified copy exists.

The cached copy authorizes only the reversible mode change. A fresh complete
backup is still required before any asset erase/write. Pure read-only backups
and first-frame inspection do not use this helper.
"""
import json
import logging

from backup_store import device_home
from device_profiles import check_manifest_device
from lg700_core import restore_file, is_empty_boot

log = logging.getLogger('lg700.maintenance')


def cached_safety_backup(device):
    """Choose a complete, hashed, same-device/revision copy; never trust a name."""
    home = device_home(device)
    if not home.is_dir():
        return None
    snapshots = sorted((p for p in home.iterdir() if p.is_dir()
                        and not p.name.endswith('.pending')), reverse=True)
    for directory in snapshots:
        try:
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
            # Historical manifests without a persisted device identity cannot
            # authorize a mode change before taking the fresh live backup.
            if not manifest.get('device', {}).get('device_key'):
                continue
            check_manifest_device(manifest, device.profile,
                                  revision=device.revision, info=device.info)
            restore_file(directory, 1, profile=device.profile, allow_empty=True)
            restore_file(directory, 0, profile=device.profile)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        return directory
    return None


def backup_before_update(device, directory, progress=lambda name, p: None):
    """Return (manifest, paused), or abort without permitting an asset write.

    Existing copies can describe older asset bytes, including an empty boot
    slot. They protect the mode transition only; they never replace this live
    backup or authorize an erase. No cached copy means the original read-only
    backup path, rather than weakening the full-backup requirement.
    """
    safety = cached_safety_backup(device)
    paused = False
    boot_was_empty = False
    if safety is not None:
        boot_was_empty = is_empty_boot(device.metadata(1), device.profile)
        # If sending mode 0 has an uncertain outcome, propagate it. In
        # particular, do not replay it or blindly send the opposite mode.
        device.set_offline_loop(False, safety)
        paused = True
        log.info('Native loop pause sent before fresh update backup using verified cached assets')
    else:
        log.info('No verified same-device/revision cache; taking the original read-only backup path')
    try:
        manifest = device.backup(directory, progress)
    except BaseException as error:
        # No asset write has started. Restore the intended native loop once
        # after a read failure, but never start a nonexistent boot animation.
        if paused and not boot_was_empty:
            try:
                device.set_offline_loop(True, safety)
            except Exception as resume_error:
                raise RuntimeError('写前备份失败，且恢复离线循环的单次尝试未通过；'
                                   '没有擦写素材，也没有重发模式命令。'
                                   f'备份错误：{error}；恢复错误：{resume_error}') from error
        raise
    return manifest, paused
