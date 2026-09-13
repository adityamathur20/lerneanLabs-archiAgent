"""QC operates on reopened IFC geometry, not only pre-export model metadata."""
import json
from pathlib import Path
import tempfile
import unittest

import ifcopenshell

from archiagent.ifc.author import author_ifc
from archiagent.ifc.inspect import validate_export
from checks.test_semantic_pipeline import build, review, _rectangle


class ExportValidationChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/"generated.ifc"
        reviewed = review()
        reviewed["voids"] = [_rectangle(7,7,8,8)]
        self.model = build(review=reviewed)
        author_ifc(self.model,self.path)

    def test_reopened_schema_solids_voids_and_containment_pass(self):
        report = validate_export(self.path,(self.model,))
        self.assertTrue(report["passed"],report["errors"])
        self.assertTrue(report["schema_validation"]["completed"])
        self.assertEqual(report["counts"]["IfcOpeningElement"],{"expected":1,"actual":1})
        self.assertEqual(report["counts"]["IfcDoor"],{"expected":1,"actual":1})
        self.assertTrue(all(v["volume_m3"]>0 and v["passed"] for v in report["geometry"]))
        self.assertTrue(any(v["class"]=="IfcSlab" for v in report["geometry"]))
        json.dumps(report,allow_nan=False)

    def test_lost_void_relationship_exposes_uncut_wall_volume(self):
        f = ifcopenshell.open(str(self.path))
        f.remove(f.by_type("IfcRelVoidsElement")[0])
        f.write(str(self.path))
        report = validate_export(self.path,self.model)
        self.assertFalse(report["passed"])
        codes = {e["code"] for e in report["errors"]}
        self.assertIn("invalid_void_relationship",codes)
        self.assertIn("solid_volume_mismatch",codes)

    def test_filled_slab_hole_fails_even_with_preserved_hole_metadata(self):
        f = ifcopenshell.open(str(self.path))
        slab = f.by_type("IfcSlab")[0]
        item = slab.Representation.Representations[0].Items[0]
        profile = item.SweptArea
        self.assertTrue(profile.is_a("IfcArbitraryProfileDefWithVoids"))
        item.SweptArea = f.create_entity("IfcArbitraryClosedProfileDef",ProfileType="AREA",
                                       OuterCurve=profile.OuterCurve)
        f.write(str(self.path))
        report = validate_export(self.path,(self.model,))
        self.assertFalse(report["passed"])
        self.assertTrue(any(e["code"]=="solid_volume_mismatch" and "IfcSlab" in e["entity"]
                            for e in report["errors"]))

    def test_translated_solid_fails_world_coordinate_bounds(self):
        f = ifcopenshell.open(str(self.path))
        column = f.by_type("IfcColumn")[0]
        location = column.ObjectPlacement.RelativePlacement.Location
        x,y,z = location.Coordinates
        location.Coordinates = (x+1.0,y,z)
        f.write(str(self.path))
        report = validate_export(self.path,(self.model,))
        self.assertFalse(report["passed"])
        self.assertTrue(any(e["code"]=="solid_bounds_mismatch" and "IfcColumn" in e["entity"]
                            for e in report["errors"]))


if __name__=="__main__":
    unittest.main()
