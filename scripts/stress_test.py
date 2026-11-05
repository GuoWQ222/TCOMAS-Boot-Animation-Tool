"""Repeat real app/core flows over synthetic HID reports. Never opens USB.

Uses the offline storage fixture as a backend, not LG700.__init__. Hardware
enumeration and transport opening are explicitly blocked throughout the run.
"""
import argparse
from collections import deque
from contextlib import nullcontext
import json
import logging
import math
from pathlib import Path
import queue
import random
import struct
import sys
import tempfile
import time
from types import MethodType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
import hid
import backup_store
import lg700_core as core
import lg700_tool as gui
from device_profiles import PROFILES, LG700_PROFILE, connection_identity
from error_details import describe_error
from test_empty_boot import StoredDevice, frame


class WireHID:
    """Assemble genuine 441-byte reports and return genuine 440-byte replies."""
    def __init__(self, storage):
        self.storage, self.queue, self.commands = storage, deque(), []
        self.parts, self.count, self.fault = [], 0, None

    def write(self, report):
        assert len(report) == 441 and report[0] == 0
        header = struct.unpack_from('<I', report, 1)[0]
        op = header & 255
        if header & 0x80000000:
            self.parts = []
            self.count = (header & 0x7FFFFFFF) >> 8
            assert 1 <= self.count <= core.MAX_PACKETS
        else:
            assert header == (len(self.parts) << 8 | op)
        self.parts.append(report[5:])
        if len(self.parts) != self.count:
            return 441
        payload = b''.join(self.parts)
        sizes = {0x87: 0, 0x0C: 8, 0x0D: 12, 0x0A: 8, 0x1E: 1}
        length = 12 + struct.unpack_from('<I', payload, 4)[0] if op == 0x0B else sizes[op]
        payload = payload[:length]
        self.commands.append((op, payload))
        answer = self.storage._query(op, payload)
        if op == 0x1E:
            assert answer == b''
            return 441
        count = max(1, math.ceil(len(answer) / core.CMD_PAYLOAD))
        reports = []
        for seq in range(count):
            head = (0x80000000 | count << 8 | op) if seq == 0 else (seq << 8 | op)
            reports.append(struct.pack('<I', head) +
                           answer[seq * 436:(seq + 1) * 436].ljust(436, b'\0'))
        if self.fault:
            reports = self.fault(op, payload, reports)
        self.queue.extend(reports)
        return 441

    def read(self, length, timeout_ms):
        assert length == 440 and timeout_ms > 0
        return self.queue.popleft() if self.queue else b''

    def close(self):
        self.queue.clear()


def target(profile, *, revision=0x75, empty=False):
    storage = StoredDevice(profile)
    storage.revision = revision
    if not empty:
        storage.assets[1] = core.pack_boot([frame(profile, 'navy'), frame(profile, 'teal')])
    device = object.__new__(core.LG700)
    device._profile, device.revision, device.info = profile, revision, storage.info
    device._mutex = device._kernel = None
    device.hid = WireHID(storage)
    device.close = lambda: device.hid.queue.clear()
    # Simulated reconnection only; no enumeration or native handles are allowed.
    device._reconnect_command_interface = lambda: device.hid.queue.clear()
    return device


def app_for(device):
    app = SimpleNamespace(device_identity=connection_identity(device.info), events=queue.Queue(),
        target_profile=lambda: device.profile, vendor_session=nullcontext,
        note=lambda *args: None, stage=lambda name: None, run=lambda job: job())
    app.enable_offline = MethodType(gui.App.enable_offline, app)
    return app


def unsafe_commands(device):
    return [(op, payload) for op, payload in device.hid.commands if op in (0x0A, 0x0B)]


def healthy_cycles(directory, repetitions, result):
    configs = [(p, rev, empty) for p in PROFILES
               for rev in ((0x67, 0x75) if p == LG700_PROFILE else (0x75,))
               for empty in (False, True)]
    for index, (profile, revision, empty) in enumerate(configs):
        home = directory / f'config-{index}'
        home.mkdir()
        device = target(profile, revision=revision, empty=empty)
        wire = device.hid
        source_device = target(profile, revision=revision)
        source = home / 'factory-source'
        source_device.backup(source)
        expected = dict(source_device.hid.storage.assets)
        app = app_for(device)
        with patch.object(backup_store, 'data_home', return_value=home / 'store'), \
             patch.object(gui, 'LG700', return_value=device), \
             patch.object(gui, 'original_directory', return_value=source):
            original_bytes = None
            for cycle in range(repetitions):
                if empty and cycle == 0:
                    try:
                        device.read_boot_first_frame()
                    except ValueError as error:
                        assert '尚未保存' in str(error)
                    else:
                        raise AssertionError('empty first frame was accepted')
                else:
                    snapshot = device.read_boot_first_frame()
                    assert snapshot['jpeg'] == core.unpack_boot(wire.storage.assets[1], profile=profile)[0]
                before = len(unsafe_commands(device))
                device.backup(home / f'manual-{cycle}')
                assert len(unsafe_commands(device)) == before
                color = f'#{cycle * 10007 % 0xFFFFFF:06x}'
                jpeg = frame(profile, color)
                data = core.pack_boot([jpeg] * (1 + cycle % 12))
                gui.App.write_data(app, data)
                assert wire.storage.assets == {1: data, 0: jpeg}
                original = backup_store.device_home(device) / 'Original'
                current_original = {p.name: p.read_bytes() for p in original.glob('*.bin')}
                if original_bytes is None:
                    original_bytes = current_original
                assert current_original == original_bytes
                identical = home / f'identical-{cycle}'
                device.backup(identical)
                count = len(unsafe_commands(device))
                for selector in (1, 0):
                    device.write_asset(selector, wire.storage.assets[selector], identical)
                    assert device.last_write_skipped
                    result['identical_asset_writes_skipped'] += 1
                assert len(unsafe_commands(device)) == count
                wire.commands.clear()
                gui.App.restore(app)
                assert wire.storage.assets == expected
                modes = [payload for op, payload in wire.commands if op == 0x1E]
                assert modes == [b'\0'], modes
                while not app.events.empty():
                    app.events.get_nowait()
                result['full_write_restore_cycles'] += 1
        result['configurations'].append(dict(model=profile.name, revision=f'{revision:04X}',
                                              initially_empty=empty, cycles=repetitions))


def fault_cycles(directory, repetitions, result):
    failures = ('erase_timeout', 'erase_rejected', 'data_timeout', 'commit_timeout',
                'negative_write_status', 'corrupt_readback', 'persistent_read_timeout',
                'transient_read_timeout', 'transient_wrong_sequence')
    for fault_name in failures:
        for index in range(repetitions):
            device = target(LG700_PROFILE)
            wire = device.hid
            source = directory / f'{fault_name}-{index}'
            device.backup(source)
            # Enough payload to cross several HID reports and write blocks.
            jpeg = frame(LG700_PROFILE, f'#{(index + 1) * 12347 % 0xFFFFFF:06x}')
            data = core.pack_boot([jpeg] * (3 + index % 7))
            wire.commands.clear()
            armed, injected = False, False
            def fault(op, payload, reports):
                nonlocal armed, injected
                if op == 0x0A:
                    armed = True
                    if fault_name == 'erase_timeout':
                        return []
                    if fault_name == 'erase_rejected':
                        return [reports[0][:4] + struct.pack('<Ii', 0, -1) + reports[0][12:]]
                if op == 0x0B:
                    offset, length, percent = struct.unpack_from('<III', payload)
                    if fault_name == 'data_timeout' or (fault_name == 'commit_timeout' and percent == 100):
                        return []
                    if fault_name == 'negative_write_status':
                        return [reports[0][:12] + struct.pack('<i', -5) + reports[0][16:]]
                if armed and op == 0x0D:
                    if fault_name == 'persistent_read_timeout':
                        return []
                    if fault_name == 'transient_read_timeout' and not injected:
                        injected = True
                        return []
                    if fault_name == 'transient_wrong_sequence' and not injected:
                        injected = True
                        assert len(reports) > 1
                        reports[1] = struct.pack('<I', 0x0000020D) + reports[1][4:]
                    if fault_name == 'corrupt_readback':
                        first = bytearray(reports[0])
                        first[100] ^= 1
                        reports[0] = bytes(first)
                return reports
            wire.fault = fault
            try:
                device.write_asset(1, data, source)
            except (OSError, ValueError, RuntimeError) as error:
                assert not fault_name.startswith('transient'), describe_error(error)
                message = describe_error(error)
                assert message.strip()
                result['fault_errors'][fault_name] = message
                result['injected_failures_stopped'] += 1
            else:
                assert fault_name.startswith('transient'), fault_name
                assert wire.storage.assets[1] == data
                result['transient_read_faults_recovered'] += 1
            commands = unsafe_commands(device)
            assert sum(op == 0x0A for op, _ in commands) == 1
            offsets = [struct.unpack_from('<I', payload)[0] for op, payload in commands if op == 0x0B]
            assert len(offsets) == len(set(offsets)), 'unsafe write was replayed'
            result['fault_scenarios'] += 1


def capacity_cycles(directory, result):
    for profile in PROFILES:
        device = target(profile)
        backup = directory / profile.key
        device.backup(backup)
        jpeg = frame(profile, 'purple')
        count = min(4095, core.capacity(1, profile) // (len(jpeg) + 24))
        data = core.pack_boot([jpeg] * count)
        while len(data) > core.capacity(1, profile):
            count -= 1
            data = core.pack_boot([jpeg] * count)
        device.write_asset(1, data, backup)
        assert device.hid.storage.assets[1] == data
        assert len(data) > core.capacity(1, profile) * .99
        result['near_capacity'].append(dict(model=profile.name, frames=count, bytes=len(data),
                                             full_readback_match=True))


def parser_mutations(repetitions, result):
    rng = random.Random(1211)
    good = core.pack_boot([frame(LG700_PROFILE, 'orange')] * 4)
    for index in range(repetitions):
        data = bytearray(good)
        kind = index % 8
        if kind == 0:
            del data[-rng.randint(1, len(data) - 1):]
        elif kind == 1:
            data[rng.randrange(4)] ^= 1 << rng.randrange(8)
        elif kind in (2, 3):
            _, size, table, prefix, flags = struct.unpack_from('<IIIHH', data)
            if kind == 2:
                prefix = rng.choice((0, 2, 65535))
            else:
                flags = 0x2000 | (flags & 0xFFF)
            struct.pack_into('<IIIHH', data, 0, core.checksum_record(size, table, prefix, flags),
                             size, table, prefix, flags)
        elif kind in (4, 5):
            _, offset, length, name, flags = struct.unpack_from('<IIIHH', data, 17)
            if kind == 4:
                data[offset + name - 1] = rng.randint(1, 255)
            else:
                offset = len(data) + rng.randint(1, 65535)
                struct.pack_into('<IIIHH', data, 17, core.checksum_record(offset, length, name, flags),
                                 offset, length, name, flags)
        elif kind == 6:
            data[-rng.randint(1, 16)] ^= 1 << rng.randrange(8)
        else:
            _, offset, length, name, _ = struct.unpack_from('<IIIHH', data, 17)
            data[offset + name + rng.randrange(length)] ^= 1 << rng.randrange(8)
        try:
            core.validate_asset(1, bytes(data))
        except (ValueError, OSError):
            result['parser_mutations_rejected'] += 1
        else:
            # Some entropy-bit changes are valid JPEGs. Acceptance is allowed
            # only after the same complete decoding used by real asset writes.
            result['parser_mutations_valid_after_decode'] += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=15)
    parser.add_argument('--fault-repetitions', type=int, default=20)
    parser.add_argument('--mutations', type=int, default=2000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert min(args.cycles, args.fault_repetitions, args.mutations) > 0
    started = time.monotonic()
    result = dict(version=gui.APP_VERSION, hardware_access=False, real_vendor_processes_touched=False,
        simulated_delays=True, full_write_restore_cycles=0, identical_asset_writes_skipped=0,
        configurations=[], fault_scenarios=0, injected_failures_stopped=0,
        transient_read_faults_recovered=0, fault_errors={}, near_capacity=[],
        parser_mutations_rejected=0, parser_mutations_valid_after_decode=0)
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix='tcomas-offline-stress-') as folder, \
         patch.object(hid, 'enumerate', side_effect=AssertionError('Physical USB enumeration forbidden')), \
         patch.object(core, 'open_command_transport', side_effect=AssertionError('Physical USB forbidden')), \
         patch.object(gui, 'pause_vendor', side_effect=AssertionError('Real process suspension forbidden')), \
         patch.object(core.time, 'sleep'):
        directory = Path(folder)
        healthy_cycles(directory, args.cycles, result)
        fault_cycles(directory, args.fault_repetitions, result)
        capacity_cycles(directory, result)
        parser_mutations(args.mutations, result)
    result['seconds'] = round(time.monotonic() - started, 3)
    result['passed'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('fault_errors', 'configurations')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
