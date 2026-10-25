"""Run the offline test suite without requiring installation or hardware."""
from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'src'), str(root / 'tests')]
suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
