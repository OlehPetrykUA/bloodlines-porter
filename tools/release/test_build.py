import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "build.sh"


def nice_line():
    return next(l for l in SCRIPT.read_text().splitlines() if "renice" in l)


class Priority(unittest.TestCase):
    def run_nice(self, start):
        out = subprocess.run(["sh", "-c", "%s\n%s\nps -o nice= -p $$" % ("renice -n %d -p $$ >/dev/null" % start if start else ":", nice_line())], capture_output=True, text=True, check=True).stdout
        return int(out.split()[0])

    def test_plain_nice_10_from_default(self):
        self.assertEqual(self.run_nice(0), 10)

    def test_a_nicer_caller_is_left_alone(self):
        self.assertEqual(self.run_nice(15), 15)

    def test_no_background_qos(self):
        self.assertNotIn("taskpolicy", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
