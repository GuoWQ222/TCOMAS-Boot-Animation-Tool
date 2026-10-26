"""Per-user UI preferences, separate from device backups and media."""
import json
import logging
from pathlib import Path
from backup_store import data_home


def preferences_path():
    return Path(data_home()) / 'ui-preferences.json'


def needs_startup_notice():
    try:
        settings = json.loads(preferences_path().read_text(encoding='utf8'))
        return not (isinstance(settings, dict) and settings.get('notice_read') is True)
    except (OSError, ValueError, TypeError):
        return True


def remember_notice_read():
    path = preferences_path()
    temporary = path.with_suffix('.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({'notice_read': True}, indent=2), encoding='utf8')
        temporary.replace(path)
    except OSError:
        # A read-only profile should not block use of the application.
        logging.getLogger('lg700.ui').warning('Cannot save reminder preference', exc_info=True)
