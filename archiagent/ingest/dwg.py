"""DWG -> DXF via the ODA File Converter.

Three things about that converter drive this module's shape, all measured
against it on 2026-09-27 rather than assumed:

1. It exits 0 even when conversion fails, writing `<name>.dxf.err` beside the
   output instead. The return code is not a signal; the presence of the output
   and the absence of a `.err` are.
2. It converts DIRECTORIES, not files, so the input is staged alone in a
   private temp directory — otherwise a batch run picks up neighbours, and two
   concurrent conversions write into each other's folder.
3. It is a GUI application bundle and needs a windowing session on macOS.

`$INSUNITS` in the output is not trusted: `--units-per-foot` stays
authoritative, exactly as it is for a hand-exported DXF.

Output is deterministic apart from the `$TDUPDATE`/`$TDUUPDATE` header
timestamps, so source identity comes from `archiagent.ingest.digest`, not a
byte hash. The converted DXF is kept beside the outputs for debugging, not
because replay depends on it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ODA_DEFAULT = Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")

#: ASCII DXF, R2018 — what ezdxf reads and what the unit override assumes.
OUTPUT_VERSION = "ACAD2018"
OUTPUT_FORMAT = "DXF"


class DwgConversionError(RuntimeError):
    """Conversion did not produce a usable DXF."""


def converter_path() -> Path:
    return Path(os.environ.get("ARCHIAGENT_ODA_CONVERTER", str(ODA_DEFAULT)))


def converter_available() -> bool:
    """Whether DWG conversion can work here at all.

    Callers that accept uploads use this to refuse a DWG up front, rather than
    queue a job that fails minutes later on a worker with no converter.
    """
    return converter_path().exists()


def convert_dwg(dwg_path: Path, out_dir: Path, *, timeout_s: int = 300) -> Path:
    """Convert one DWG, returning the path of the DXF written into `out_dir`."""
    dwg_path = Path(dwg_path)
    if not dwg_path.is_file():
        raise DwgConversionError(f"no such DWG: {dwg_path}")

    converter = converter_path()
    if not converter.exists():
        raise DwgConversionError(
            f"ODA File Converter not found at {converter}. Install it, or set "
            "ARCHIAGENT_ODA_CONVERTER to its path."
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as staging_root:
        staging = Path(staging_root)
        shutil.copy2(dwg_path, staging / dwg_path.name)
        try:
            completed = subprocess.run(
                [str(converter), str(staging), str(out_dir),
                 OUTPUT_VERSION, OUTPUT_FORMAT, "0", "1", "*.DWG"],
                capture_output=True, text=True, timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as expired:
            raise DwgConversionError(
                f"ODA File Converter timed out after {timeout_s}s on {dwg_path.name}"
            ) from expired

    produced = out_dir / f"{dwg_path.stem}.dxf"
    failure = out_dir / f"{dwg_path.stem}.dxf.err"

    if failure.exists():
        detail = failure.read_text(errors="replace").strip()
        # Never leave a half-written DXF for the pipeline to mis-parse.
        produced.unlink(missing_ok=True)
        raise DwgConversionError(f"ODA could not read {dwg_path.name}: {detail}")
    if not produced.is_file() or produced.stat().st_size == 0:
        # No output AND no .err is the shape a headless worker sees, because
        # ODA is a GUI bundle that needs a windowing session. Reporting only
        # "produced no DXF" would blame the drawing for a deployment fault, so
        # the converter's own output goes into the message.
        detail = " ".join(
            part.strip() for part in (completed.stderr, completed.stdout) if part and part.strip()
        )
        raise DwgConversionError(
            f"ODA produced no DXF for {dwg_path.name} (exit {completed.returncode})"
            + (f": {detail[-800:]}" if detail else
               ". The converter wrote nothing; it needs a windowing session, "
               "so this usually means it is running headless.")
        )
    return produced
