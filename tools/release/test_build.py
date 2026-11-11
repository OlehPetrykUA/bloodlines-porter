import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "build.sh"
FAKE_PS = '#!/bin/sh\ncat "$NICE_FILE"\n'
FAKE_RENICE = '#!/bin/sh\n[ "$1" = -n ] || exit 2\necho $(( $(cat "$NICE_FILE") + $2 )) > "$NICE_FILE"\n'


def nice_line():
    return next(l for l in SCRIPT.read_text().splitlines() if "renice" in l)


class Priority(unittest.TestCase):
    def run_nice(self, start):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("ps", FAKE_PS), ("renice", FAKE_RENICE)):
                p = Path(d) / name
                p.write_text(text)
                p.chmod(0o755)
            nice = Path(d) / "nice"
            nice.write_text("%d\n" % start)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], NICE_FILE=str(nice))
            subprocess.run(["sh", "-c", nice_line()], env=env, capture_output=True, text=True, check=True)
            return int(nice.read_text().split()[0])

    def test_plain_nice_10_from_default(self):
        self.assertEqual(self.run_nice(0), 10)

    def test_a_nicer_caller_is_left_alone(self):
        self.assertEqual(self.run_nice(15), 15)

    def test_no_background_qos(self):
        self.assertNotIn("taskpolicy", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
