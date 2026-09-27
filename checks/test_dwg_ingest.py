import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archiagent.ingest.dwg import DwgConversionError, convert_dwg, converter_path

DWG = os.environ.get("ARCHIAGENT_DWG")


class DwgIngestChecks(unittest.TestCase):
    def test_missing_converter_is_reported_at_the_boundary(self):
        """Review Focus #4: a missing binary must not surface as a
        FileNotFoundError deep inside a queued job minutes later."""
        with TemporaryDirectory() as out, TemporaryDirectory() as src:
            dwg = Path(src) / "plan.dwg"
            dwg.write_bytes(b"not really a dwg")
            os.environ["ARCHIAGENT_ODA_CONVERTER"] = "/nonexistent/ODAFileConverter"
            try:
                with self.assertRaises(DwgConversionError) as caught:
                    convert_dwg(dwg, Path(out))
                self.assertIn("not found", str(caught.exception).lower())
            finally:
                del os.environ["ARCHIAGENT_ODA_CONVERTER"]

    def test_a_missing_input_is_rejected_before_launching_anything(self):
        with TemporaryDirectory() as out:
            with self.assertRaises(DwgConversionError):
                convert_dwg(Path("/nonexistent/plan.dwg"), Path(out))

    @unittest.skipUnless(DWG and Path(DWG).exists(), "set ARCHIAGENT_DWG to a real .dwg")
    def test_a_real_dwg_converts_to_a_readable_dxf(self):
        with TemporaryDirectory() as out:
            dxf = convert_dwg(Path(DWG), Path(out))
            self.assertTrue(dxf.exists())
            self.assertEqual(dxf.suffix, ".dxf")
            # ASCII DXF R2018 starts with a SECTION marker.
            self.assertIn(b"SECTION", dxf.read_bytes()[:4096])

    @unittest.skipUnless(DWG and Path(DWG).exists(), "set ARCHIAGENT_DWG to a real .dwg")
    def test_a_name_with_spaces_survives(self):
        """Review Focus #2: every real drawing in this corpus has spaces."""
        with TemporaryDirectory() as out, TemporaryDirectory() as src:
            spaced = Path(src) / "Ground Floor Plan rev B.dwg"
            spaced.write_bytes(Path(DWG).read_bytes())
            dxf = convert_dwg(spaced, Path(out))
            self.assertEqual(dxf.stem, "Ground Floor Plan rev B")

    @unittest.skipUnless(converter_path().exists(), "ODA File Converter not installed")
    def test_an_unreadable_dwg_is_not_reported_as_success(self):
        """Review Focus #1. ODA exits 0 and writes <name>.dxf.err, so the
        return code is never the signal."""
        with TemporaryDirectory() as out, TemporaryDirectory() as src:
            junk = Path(src) / "broken.dwg"
            junk.write_bytes(b"AC1032" + b"\x00" * 512)
            with self.assertRaises(DwgConversionError) as caught:
                convert_dwg(junk, Path(out))
            self.assertTrue(str(caught.exception))
            self.assertFalse((Path(out) / "broken.dxf").exists())


if __name__ == "__main__":
    unittest.main()
