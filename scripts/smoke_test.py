"""Focused Submission UI/document smoke check; no external services."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_submission_layout import SubmissionLayoutTests

if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SubmissionLayoutTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
