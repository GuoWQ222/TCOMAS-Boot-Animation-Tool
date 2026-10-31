"""Wire-level regressions for V75 stale-slot responses and safe recovery."""
from collections import deque
import math
import struct
import unittest
from unittest.mock import Mock, patch

import lg700_core as core


class ReplayHID:
    def __init__(self, *, missing_read_headers=0, bad_probes=0, changed_after_fault=False):
        self.asset = bytes(range(256)) * 40
        self.queue = deque()
        self.commands = []
        self.parts = []
        self.missing_read_headers = missing_read_headers
        self.bad_probes = bad_probes
        self.changed_after_fault = changed_after_fault
        self.had_fault = False
        self.timeouts = []

    def write(self, report):
        head = struct.unpack_from('<I', report, 1)[0]
        if head & 0x80000000:
            self.parts = []
            self.count = (head & 0x7FFFFFFF) >> 8
        self.parts.append(report[5:])
        if len(self.parts) == self.count:
            op = head & 255
            payload = b''.join(self.parts)
            self.commands.append(op)
            if op == 0x1E:
                return 441
            if op == 0x87:
                answer = struct.pack('<H', 0x75)
            elif op == 0x0C:
                selector = struct.unpack_from('<I', payload)[0]
                next_record = 0x420000 if self.had_fault and self.changed_after_fault else 0x410000
                answer = struct.pack('<IiIII', selector, len(self.asset), 0x400000, next_record, 0)
            elif op == 0x0D:
                offset, length, _ = struct.unpack_from('<IIi', payload)
                answer = struct.pack('<IIi', offset, length, 0) + self.asset[offset:offset + length]
            else:
                # A wrong response to an unsafe command must never trigger replay.
                self.queue.append(struct.pack('<I', 0x0000040D).ljust(440, b'\0'))
                return 441
            count = math.ceil(len(answer) / 436)
            reports = []
            for seq in range(count):
                header = (0x80000000 | count << 8 | op) if seq == 0 else (seq << 8 | op)
                reports.append(struct.pack('<I', header) + answer[seq * 436:(seq + 1) * 436].ljust(436, b'\0'))
            bad = op == 0x0D and self.missing_read_headers
            if bad:
                self.missing_read_headers -= 1
            if op in (0x87, 0x0C) and self.bad_probes and self.had_fault:
                self.bad_probes -= 1
                bad = True
            if bad:
                self.had_fault = True
                reports[0] = struct.pack('<I', 0x0000040D).ljust(440, b'\0')
            self.queue.extend(reports)
        return 441

    def read(self, length, timeout_ms):
        self.timeouts.append(timeout_ms)
        return self.queue.popleft() if self.queue else b''


def device(transport):
    result = object.__new__(core.LG700)
    result.hid = transport
    result._profile = core.LG700_PROFILE
    return result


class ResponseResyncTests(unittest.TestCase):
    @patch.object(core.time, 'sleep')
    def test_missing_first_read_report_and_two_bad_metadata_probes_recover(self, sleep):
        hid = ReplayHID(missing_read_headers=1, bad_probes=2)
        target = device(hid)
        actual, meta = target.read_asset(1)
        self.assertEqual(actual, hid.asset)
        self.assertEqual(meta['size'], len(hid.asset))
        self.assertEqual(hid.commands, [0x0C, 0x0D, 0x0C, 0x0C, 0x0C, 0x0D, 0x0D, 0x0C])
        self.assertEqual(target.transport_resyncs, 2)
        self.assertTrue(all(op in (0x87, 0x0C, 0x0D) for op in hid.commands))

    @patch.object(core.time, 'sleep')
    def test_missing_read_header_and_changed_record_abort_before_retrying_asset(self, sleep):
        hid = ReplayHID(missing_read_headers=1, changed_after_fault=True)
        with self.assertRaisesRegex(OSError, '素材发生变化'):
            device(hid).read_asset(1)
        self.assertEqual(hid.commands, [0x0C, 0x0D, 0x0C])

    @patch.object(core.time, 'sleep')
    def test_failed_metadata_probes_have_a_fixed_attempt_limit(self, sleep):
        hid = ReplayHID(bad_probes=100)
        hid.had_fault = True
        with self.assertRaises(core.ResponseSyncError):
            device(hid).metadata(1)
        self.assertEqual(hid.commands, [0x0C] * 3)

    @patch.object(core.time, 'sleep')
    def test_version_probe_can_resync_during_initial_connection(self, sleep):
        hid = ReplayHID(bad_probes=1)
        hid.had_fault = True
        actual = device(hid).query(0x87)
        self.assertEqual(struct.unpack_from('<H', actual)[0], 0x75)
        self.assertEqual(hid.commands, [0x87, 0x87])

    @patch.object(core.time, 'sleep')
    def test_persistently_missing_asset_headers_stop_after_two_reads(self, sleep):
        hid = ReplayHID(missing_read_headers=100)
        with self.assertRaisesRegex(OSError, '已停止本次操作'):
            device(hid).read_asset(1)
        self.assertEqual(hid.commands.count(0x0D), 2)

    @patch.object(core.time, 'sleep')
    def test_erase_and_data_write_are_never_repeated_on_stale_response(self, sleep):
        for op, payload in ((0x0A, struct.pack('<II', 1, 8192)),
                            (0x0B, struct.pack('<III', 0, 8192, 99) + b'x' * 8192),
                            (0x0B, struct.pack('<III', 0, 32, 100) + b'x' * 32)):
            with self.subTest(op=op, payload_length=len(payload)):
                hid = ReplayHID()
                with self.assertRaises(core.ResponseSyncError):
                    device(hid).query(op, payload)
                self.assertEqual(hid.commands, [op])

    @patch.object(core.time, 'sleep')
    def test_mode_command_is_sent_once_without_reading_or_retrying(self, sleep):
        hid = ReplayHID()
        self.assertEqual(device(hid).query(0x1E, b'\x01'), b'')
        self.assertEqual(hid.commands, [0x1E])
        self.assertEqual(hid.timeouts, [])

    def test_stray_report_before_valid_header_does_not_invent_a_failure(self):
        hid = ReplayHID()
        hid.queue.append(struct.pack('<I', 0x0000040D).ljust(440, b'\0'))
        self.assertEqual(struct.unpack_from('<H', device(hid).query(0x87))[0], 0x75)
        self.assertEqual(hid.commands, [0x87])
        self.assertEqual(hid.timeouts[1], core.RESYNC_QUIET_MS)

    def test_continuous_old_reports_cannot_drain_forever(self):
        hid = Mock()
        hid.read.return_value = b'old report'
        with self.assertRaisesRegex(OSError, '持续返回旧数据'):
            device(hid)._drain_input()
        self.assertEqual(hid.read.call_count, core.MAX_PACKETS * 2)

    @patch.object(core.time, 'sleep')
    def test_disconnected_transport_is_not_automatically_reopened_or_replayed(self, sleep):
        hid = Mock()
        hid.write.side_effect = OSError('device disconnected')
        with self.assertRaisesRegex(OSError, 'device disconnected'):
            device(hid).query(0x87)
        hid.write.assert_called_once()
        hid.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
