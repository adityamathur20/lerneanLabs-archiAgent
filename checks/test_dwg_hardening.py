"""Tests for the Phase 4 review findings. Each reproduces a defect first."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archiagent.ingest.dwg import (
    DwgConversionError,
    convert_dwg,
    converter_available,
    converter_path,
)

DWG = os.environ.get("ARCHIAGENT_DWG")


def _run(*args):
    return subprocess.run([sys.executable, "-m", "archiagent", *args],
                          capture_output=True, text=True)


class DwgHardeningChecks(unittest.TestCase):
    def test_converter_availability_is_queryable(self):
        """The service must be able to refuse DWG uploads up front rather than
        queue a job that fails minutes later on a worker with no converter."""
        self.assertIsInstance(converter_available(), bool)
        os.environ["ARCHIAGENT_ODA_CONVERTER"] = "/nonexistent/ODAFileConverter"
        try:
            self.assertFalse(converter_available())
        finally:
            del os.environ["ARCHIAGENT_ODA_CONVERTER"]

    def test_a_silent_converter_failure_reports_real_diagnostics(self):
        """The no-output branch is what a headless or Linux worker hits: ODA is
        a GUI bundle, so it can exit without writing either a DXF or a .err.
        Reporting only "produced no DXF" would blame the user's drawing for
        what is actually a deployment fault.

        The subprocess is stubbed because a working ODA cannot be made to fail
        this way — on this machine even junk input produces a .err.
        """
        import subprocess as real_subprocess

        from archiagent.ingest import dwg as module

        class Silent:
            returncode = 127
            stdout = ""
            stderr = "qt.qpa.plugin: could not connect to display"

        original = module.subprocess.run
        module.subprocess.run = lambda *a, **k: Silent()
        try:
            with TemporaryDirectory() as out, TemporaryDirectory() as src:
                dwg = Path(src) / "plan.dwg"
                dwg.write_bytes(b"AC1032" + b"\x00" * 64)
                with self.assertRaises(DwgConversionError) as caught:
                    convert_dwg(dwg, Path(out))
                message = str(caught.exception)
                self.assertIn("127", message)
                self.assertIn("could not connect to display", message)
        finally:
            module.subprocess.run = original
            assert module.subprocess.run is real_subprocess.run

    @unittest.skipUnless(DWG and Path(DWG).exists(), "set ARCHIAGENT_DWG to a real .dwg")
    def test_a_frozen_dwg_interpretation_can_be_replayed(self):
        """ODA's DXF output is not byte-deterministic, so checksumming the
        CONVERTED file makes every DWG replay fail its own gate. The converted
        DXF is therefore kept beside the outputs and replayed against."""
        with TemporaryDirectory() as root:
            out = Path(root) / "out"
            frozen = _run("--dwgFilePath", DWG, "--outputDir", str(out),
                          "--walls", "WALLS", "--units-per-foot", "12", "--freeze-only")
            self.assertEqual(frozen.returncode, 0, frozen.stderr)

            manifest = next(out.glob("*.interpretation.json"))
            converted = next(out.glob("*.dxf"), None)
            self.assertIsNotNone(converted, f"converted DXF not kept: {sorted(p.name for p in out.iterdir())}")

            replayed = _run("--dxfFilePath", str(converted), "--outputDir", str(Path(root) / "out2"),
                            "--replay-manifest", str(manifest))
            self.assertEqual(replayed.returncode, 0, replayed.stderr)

    def test_usage_errors_are_reported_before_paying_for_conversion(self):
        """Forgetting --outputDir must not cost a full DWG conversion first."""
        with TemporaryDirectory() as src:
            dwg = Path(src) / "plan.dwg"
            dwg.write_bytes(b"AC1032" + b"\x00" * 64)
            result = _run("--dwgFilePath", str(dwg))
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("outputDir", result.stderr)


if __name__ == "__main__":
    unittest.main()
