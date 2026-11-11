import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "build.sh"


def nice_line():
    return next(l for l in SCRIPT.read_text().splitlines() if "renice" in l)


class Priority(unittest.TestCase):
    def check(self, start):
        setup = "renice %d -p $$ >/dev/null 2>&1" % start if start is not None else ":"
        script = "%s\nps -o nice= -p $$\n%s\nps -o nice= -p $$" % (setup, nice_line())
        before, after = (int(v) for v in subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True).stdout.split())
        self.assertEqual(after, max(before, 10), "nice %d before, %d after" % (before, after))

    def test_plain_nice_10_from_default(self):
        self.check(None)

    def test_nice_10_from_zero(self):
        self.check(0)

    def test_a_nicer_caller_is_left_alone(self):
        self.check(15)

    def test_absolute_not_relative(self):
        self.assertNotIn("renice -n", nice_line())

    def test_no_background_qos(self):
        self.assertNotIn("taskpolicy", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
