import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

DWG = os.environ.get("ARCHIAGENT_DWG")


def _run(*args):
    return subprocess.run([sys.executable, "-m", "archiagent", *args],
                          capture_output=True, text=True)


class DwgCliChecks(unittest.TestCase):
    def test_dwg_flag_is_advertised(self):
        self.assertIn("--dwgFilePath", _run("--help").stdout)

    def test_two_inputs_are_refused(self):
        """Exactly one input flag, the same rule the other two already follow."""
        result = _run("--dwgFilePath", "a.dwg", "--dxfFilePath", "b.dxf", "--inspect")
        self.assertEqual(result.returncode, 3)

    def test_no_input_is_still_refused(self):
        self.assertEqual(_run("--inspect").returncode, 3)

    def test_an_unconvertible_dwg_fails_with_the_converters_message(self):
        with TemporaryDirectory() as src:
            junk = Path(src) / "broken.dwg"
            junk.write_bytes(b"AC1032" + b"\x00" * 512)
            result = _run("--dwgFilePath", str(junk), "--inspect")
            self.assertEqual(result.returncode, 1)
            self.assertIn("broken.dwg", result.stderr)

    @unittest.skipUnless(DWG and Path(DWG).exists(), "set ARCHIAGENT_DWG to a real .dwg")
    def test_a_real_dwg_is_inspected_like_a_dxf(self):
        """The whole point: after conversion the DWG follows the DXF path
        exactly, so --inspect prints the same layer inventory."""
        result = _run("--dwgFilePath", DWG, "--inspect")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("layer", result.stdout)
        self.assertIn("WALLS", result.stdout)


if __name__ == "__main__":
    unittest.main()
