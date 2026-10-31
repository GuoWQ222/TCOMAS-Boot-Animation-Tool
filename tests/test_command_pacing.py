"""Ensure every command, including a failed one, leaves firmware cleanup time."""
import unittest
from unittest.mock import Mock, patch
from lg700_core import LG700


class CommandPacingTests(unittest.TestCase):
    def test_next_command_waits_for_the_remaining_interval(self):
        device = object.__new__(LG700)
        device._last_query_finished = 10.0
        device._query = Mock(return_value=b'accepted')
        with patch('lg700_core.time.monotonic', side_effect=(10.003, 10.02)), \
                patch('lg700_core.time.sleep') as sleep:
            self.assertEqual(device.query(0x87), b'accepted')
        self.assertAlmostEqual(sleep.call_args.args[0], .012)
        device._query.assert_called_once_with(0x87, b'', 6000)

    def test_failed_output_is_recorded_but_not_retried(self):
        device = object.__new__(LG700)
        device._query = Mock(side_effect=OSError('uncertain device response'))
        with patch('lg700_core.time.monotonic', side_effect=(10.0, 10.1)), \
                patch('lg700_core.time.sleep') as sleep:
            with self.assertRaises(OSError):
                device.query(0x0B, b'one write')
        device._query.assert_called_once_with(0x0B, b'one write', 6000)
        sleep.assert_not_called()
        self.assertEqual(device._last_query_finished, 10.1)


if __name__ == '__main__':
    unittest.main()
