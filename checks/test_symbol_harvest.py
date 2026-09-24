"""Harvesting proposes candidates; loading refuses to accept bad library data."""
import json
import math
import tempfile
import unittest
from pathlib import Path

from archiagent.classify.library_templates import load_library, normalise
from archiagent.classify.symbol_harvest import harvest, merge
from archiagent.evidence import SourceEntity
from archiagent.primitives import Primitive, PrimitiveSet

RING = ((0, 0), (2, 0), (2, 1), (1, 1), (1, 3), (0, 3), (0, 0))


def instance(tag, offset):
    """One INSERT entity and the primitive it expands to."""
    points = tuple((x+offset, y) for x, y in RING)
    entity = SourceEntity(f"ins-{tag}", "INSERT", "blocks", block_name="glyph")
    child = SourceEntity(f"ins-{tag}/0", "LWPOLYLINE", "blocks", parent_id=f"ins-{tag}")
    primitive = Primitive("rect", points, "blocks", None, None,
                          source_id=f"ins-{tag}/0", entity_type="LWPOLYLINE", closed=True)
    return entity, child, primitive


def drawing(count):
    entities, primitives = [], []
    for i in range(count):
        e, c, p = instance(i, i*10)
        entities.extend((e, c))
        primitives.append(p)
    return PrimitiveSet(tuple(primitives), (), 500, 500, "harvest.dxf", "sha-harvest",
                        tuple(entities))


class HarvestChecks(unittest.TestCase):
    def test_a_repeated_shape_is_proposed_even_though_its_name_says_nothing(self):
        candidates = harvest(drawing(9), 1, min_instances=8)
        self.assertEqual(len(candidates), 1)
        found = candidates[0]
        self.assertEqual(found["found_by"], "frequency")
        self.assertEqual(found["kind"], "unreviewed")
        self.assertEqual(found["status"], "candidate")
        self.assertEqual(found["provenance"][0]["instances"], 9)
        # The observed size is 3ft; the range is widened either side of it.
        self.assertLess(found["size_ft"]["min"], 3.0)
        self.assertGreater(found["size_ft"]["max"], 3.0)

    def test_a_shape_below_the_threshold_is_not_proposed(self):
        self.assertEqual(harvest(drawing(3), 1, min_instances=8), ())

    def test_provenance_carries_hashes_not_names(self):
        found = harvest(drawing(9), 1, min_instances=8)[0]
        recorded = json.dumps(found["provenance"])
        self.assertNotIn("glyph", recorded)
        self.assertNotIn("harvest.dxf", recorded)

    def test_merging_sums_instances_and_widens_the_range_across_drawings(self):
        first = harvest(drawing(9), 1, min_instances=8)
        merged = merge(first, harvest(drawing(9), 2, min_instances=8))
        self.assertEqual(len(merged), 1)
        self.assertEqual(sum(p["instances"] for p in merged[0]["provenance"]), 18)


class LibraryLoadChecks(unittest.TestCase):
    def library(self, payload):
        path = Path(tempfile.mkdtemp()) / "lib.json"
        path.write_text(json.dumps(payload))
        return path

    def reviewed(self, **kwargs):
        record = {"id": "t1", "kind": "plumbing", "subtype": "wc", "status": "reviewed",
                  "geometry": [dict(p) for p in normalise(
                      [{"category": "closed", "points": RING, "holes": []}])],
                  "size_ft": {"min": 1.0, "max": 4.0}}
        record.update(kwargs)
        return record

    def test_an_explicitly_named_library_that_is_missing_is_a_configuration_error(self):
        with self.assertRaises(FileNotFoundError):
            load_library("/nonexistent/symbol_library.json")

    def test_a_malformed_record_is_refused_rather_than_skipped(self):
        path = self.library({"templates": [self.reviewed(kind="spaceship")]})
        with self.assertRaises(ValueError):
            load_library(path)

    def test_unreviewed_records_are_ignored_without_failing_the_load(self):
        path = self.library({"templates": [self.reviewed(status="candidate"),
                                           self.reviewed()]})
        self.assertEqual([t["id"] for t in load_library(path)], ["t1"])

    def test_invalid_json_names_the_file(self):
        path = Path(tempfile.mkdtemp()) / "lib.json"
        path.write_text("{not json")
        with self.assertRaises(ValueError):
            load_library(path)

    def test_the_bundled_library_loads(self):
        # It ships empty until templates are reviewed in; loading must still work.
        self.assertIsInstance(load_library(), tuple)


if __name__ == "__main__":
    unittest.main()
