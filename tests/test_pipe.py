import subprocess
import sys
import time
import unittest

from feiyu.pipe import PipeReader


class TestThreadedPipe(unittest.TestCase):
    def test_timeout_then_data_and_eof(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys,time; time.sleep(.2); sys.stdout.buffer.write(b'hello'); sys.stdout.flush()"],
            stdout=subprocess.PIPE)
        reader = PipeReader(proc.stdout, threaded=True)
        try:
            self.assertEqual(reader.read(time.monotonic() + .05), b"")
            self.assertEqual(reader.read(time.monotonic() + 5), b"hello")
            self.assertEqual(reader.read(time.monotonic() + 5), b"")
        finally:
            reader.close()
            proc.wait(timeout=5)
            proc.stdout.close()
