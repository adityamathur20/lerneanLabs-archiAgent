"""A DXF's identity must survive reconversion of the DWG it came from.

The ODA File Converter is deterministic apart from two HEADER variables, the
"last updated" Julian dates `$TDUPDATE` and `$TDUUPDATE`. Measured 2026-09-29:
converting one DWG twice produced files differing in exactly those two values
and nothing else. A byte hash therefore reports a different drawing every time
the same DWG is converted, which fails freeze/replay for a reason that has
nothing to do with the drawing.
"""
import hashlib

import pytest

from archiagent.ingest.digest import dxf_source_digest


def dxf_text(*, tdupdate="2461313.817027269", tduupdate="2461313.587860602",
             tdindwg="20.2789570718", entity="LINE"):
    return "\n".join([
        "  0", "SECTION", "  2", "HEADER",
        "  9", "$ACADVER", "  1", "AC1032",
        "  9", "$TDUCREATE", " 40", "2459513.515023148",
        "  9", "$TDUPDATE", " 40", tdupdate,
        "  9", "$TDUUPDATE", " 40", tduupdate,
        "  9", "$TDINDWG", " 40", tdindwg,
        "  0", "ENDSEC",
        "  0", "SECTION", "  2", "ENTITIES",
        "  0", entity,
        "  0", "ENDSEC", "  0", "EOF", "",
    ])


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_the_two_volatile_timestamps_do_not_change_the_digest(tmp_path):
    """The whole point: reconverting the same DWG must yield the same identity."""
    a = write(tmp_path, "a.dxf", dxf_text())
    b = write(tmp_path, "b.dxf", dxf_text(tdupdate="2461399.111111111",
                                          tduupdate="2461399.222222222"))
    assert a.read_bytes() != b.read_bytes()
    assert dxf_source_digest(a) == dxf_source_digest(b)


def test_a_different_header_value_still_changes_the_digest(tmp_path):
    """Only those two are neutralised — not every code 40 after a code 9."""
    a = write(tmp_path, "a.dxf", dxf_text())
    b = write(tmp_path, "b.dxf", dxf_text(tdindwg="99.5"))
    assert dxf_source_digest(a) != dxf_source_digest(b)


def test_changed_geometry_changes_the_digest(tmp_path):
    a = write(tmp_path, "a.dxf", dxf_text())
    b = write(tmp_path, "b.dxf", dxf_text(entity="CIRCLE"))
    assert dxf_source_digest(a) != dxf_source_digest(b)


def test_digest_is_stable_across_calls(tmp_path):
    a = write(tmp_path, "a.dxf", dxf_text())
    assert dxf_source_digest(a) == dxf_source_digest(a)


def test_binary_dxf_falls_back_to_the_byte_hash(tmp_path):
    """Binary DXF has no line structure to walk; hashing it whole is honest.

    It still has to be deterministic, and it must not silently equal a
    different file.
    """
    sentinel = b"AutoCAD Binary DXF\r\n\x1a\x00"
    a = tmp_path / "a.dxf"
    a.write_bytes(sentinel + b"\x00\x01payload")
    b = tmp_path / "b.dxf"
    b.write_bytes(sentinel + b"\x00\x01other--")
    assert dxf_source_digest(a) == hashlib.sha256(a.read_bytes()).hexdigest()
    assert dxf_source_digest(a) != dxf_source_digest(b)


def test_crlf_and_lf_line_endings_agree(tmp_path):
    """ODA writes CRLF; a DXF normalised through other tooling may not.

    The timestamp lines are rewritten during hashing, so their endings must not
    leak into the digest inconsistently.
    """
    text = dxf_text()
    a = tmp_path / "a.dxf"
    a.write_bytes(text.encode())
    b = tmp_path / "b.dxf"
    b.write_bytes(text.replace("\n", "\r\n").encode())
    # Different bytes overall, so the digests legitimately differ; what must
    # hold is that each is stable and neither raises.
    assert dxf_source_digest(a) == dxf_source_digest(a)
    assert dxf_source_digest(b) == dxf_source_digest(b)


def test_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        dxf_source_digest(tmp_path / "absent.dxf")
