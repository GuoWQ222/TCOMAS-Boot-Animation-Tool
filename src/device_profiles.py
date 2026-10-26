"""Model-specific limits; LG600 / LX800 V75 have firmware emulation only."""
from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    name: str
    pid: int
    width: int
    height: int
    supported_revisions: frozenset[int]
    hardware_verified: bool
    visible_shape: str = 'rectangle'
    vid: int = 0x1A2C
    boot_region: tuple[int, int] = (0x400000, 0xF00000)
    standby_region: tuple[int, int] = (0xF00000, 0x1000000)

    @property
    def size(self):
        return self.width, self.height

    @property
    def dimensions(self):
        return f'{self.width}×{self.height}'

    @property
    def regions(self):
        return {1: self.boot_region, 0: self.standby_region}

    @property
    def validation_note(self):
        return '已有实机验证' if self.hardware_verified else 'V75 固件仿真通过，尚无实机验证'


LG700_PROFILE = DeviceProfile('lg700', 'LG700', 0x151C, 640, 480,
                             frozenset({0x67, 0x75}), True)
LG600_PROFILE = DeviceProfile('lg600', 'LG600', 0x1520, 480, 480,
                             frozenset({0x75}), False)
LX800_PROFILE = DeviceProfile('lx800', 'LX800', 0x151D, 480, 480,
                             frozenset({0x75}), False, visible_shape='circle')
PROFILES = (LG700_PROFILE, LG600_PROFILE, LX800_PROFILE)


def resolve_profile(profile=None):
    if profile is None:
        return LG700_PROFILE
    for known in PROFILES:
        if profile == known or (isinstance(profile, str) and
                                profile.lower() in (known.key, known.name.lower())):
            return known
    raise ValueError('不支持的屏幕型号；仅支持 LG700 / LG600 / LX800')


def profile_for_info(info):
    for profile in PROFILES:
        if info.get('vendor_id') == profile.vid and info.get('product_id') == profile.pid:
            return profile
    raise ValueError('USB VID/PID 不属于已支持型号')


def is_command_interface(info):
    return (info.get('interface_number') == 0 and info.get('usage_page') == 0xFF06
            and info.get('usage', 1) == 1)


def connection_identity(info):
    """Bind an operation to the device selected before conversion, not a new one."""
    path = info.get('path', b'')
    if isinstance(path, bytes):
        path = path.decode('utf8', errors='replace')
    return (info.get('vendor_id'), info.get('product_id'),
            info.get('serial_number') or '', str(path).lower())


def backup_device_key(info, profile):
    """Opaque persisted identity; never include raw USB identifiers in manifests."""
    path = info.get('path', b'')
    if isinstance(path, bytes):
        path = path.decode('utf8', errors='replace')
    identity = (profile.key + '|' + str(info.get('product_string', profile.name)) + '|'
                + str(info.get('serial_number') or path))
    return hashlib.sha256(identity.encode('utf8')).hexdigest()[:24]


def manifest_profile(manifest):
    device = manifest.get('device', {})
    # Old LG700 backups predate model/dimensions. They remain LG700-only.
    if not device.get('model') and not device.get('pid'):
        profile = LG700_PROFILE
    elif device.get('model'):
        profile = resolve_profile(device['model'])
    else:
        try:
            pid = int(str(device['pid']), 16)
        except (KeyError, ValueError) as error:
            raise ValueError('备份型号/PID 无效') from error
        profile = next((p for p in PROFILES if p.pid == pid), None)
        if profile is None:
            raise ValueError('备份型号不受支持')
    if device.get('pid') and str(device['pid']).upper() != f'{profile.pid:04X}':
        raise ValueError('备份型号与 PID 不一致')
    if device.get('vid') and str(device['vid']).upper() != f'{profile.vid:04X}':
        raise ValueError('备份 VID 不一致')
    if device.get('size') is not None and tuple(device['size']) != profile.size:
        raise ValueError('备份型号与分辨率不一致')
    return profile


def check_manifest_device(manifest, profile, *, revision=None, info=None):
    if manifest_profile(manifest) != profile:
        raise ValueError('备份型号与当前设备不匹配，禁止跨型号恢复')
    if revision is not None and manifest['device'].get('revision') != f'{revision:04X}':
        raise ValueError('备份固件版本不匹配')
    key = manifest.get('device', {}).get('device_key')
    if key and info is not None and key != backup_device_key(info, profile):
        raise ValueError('备份不属于当前设备，已停止')
