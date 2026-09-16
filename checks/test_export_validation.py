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


    def test_joined_walls_pass_reopened_validation(self):
        from checks.test_wall_joins_fixtures import model_with

        model = model_with([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        path = Path(self.directory.name)/"joined.ifc"
        author_ifc(model, path)
        report = validate_export(path, (model,))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["counts"]["IfcWall"], {"expected": 2, "actual": 2})

    def _joined(self, name):
        """An L corner: W000 stops at W001, which owns the intersection."""
        from checks.test_wall_joins_fixtures import model_with

        model = model_with([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        path = Path(self.directory.name)/name
        author_ifc(model, path)
        return model, path

    def _codes(self, model, path):
        return {e["code"] for e in validate_export(path, (model,))["errors"]}

    def test_downgraded_wall_class_fails(self):
        model, path = self._joined("downgrade.ifc")
        path.write_text(path.read_text().replace("IFCWALLSTANDARDCASE(", "IFCWALL("))
        self.assertIn("wall_class_mismatch", self._codes(model, path))

    def test_lost_axis_representation_fails(self):
        model, path = self._joined("axis.ifc")
        f = ifcopenshell.open(str(path))
        wall = f.by_type("IfcWall")[0]
        wall.Representation.Representations = tuple(
            r for r in wall.Representation.Representations
            if r.RepresentationIdentifier != "Axis")
        f.write(str(path))
        self.assertIn("wall_parametric_data_missing", self._codes(model, path))

    def test_polygon_profile_on_a_rectangular_run_fails(self):
        model, path = self._joined("polygon.ifc")
        f = ifcopenshell.open(str(path))
        item = next(r for r in f.by_type("IfcWall")[0].Representation.Representations
                    if r.RepresentationIdentifier == "Body").Items[0]
        rectangle = item.SweptArea
        x, y = rectangle.Position.Location.Coordinates if rectangle.Position else (0., 0.)
        hx, hy = rectangle.XDim/2, rectangle.YDim/2
        ring = [(x-hx, y-hy), (x+hx, y-hy), (x+hx, y+hy), (x-hx, y+hy), (x-hx, y-hy)]
        item.SweptArea = f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA",
            OuterCurve=f.createIfcPolyline([f.createIfcCartesianPoint(p) for p in ring]))
        f.write(str(path))
        self.assertIn("wall_profile_not_parametric", self._codes(model, path))

    def test_missing_connection_point_fails(self):
        model, path = self._joined("connection.ifc")
        f = ifcopenshell.open(str(path))
        f.by_type("IfcRelConnectsPathElements")[0].ConnectionGeometry = None
        f.write(str(path))
        self.assertIn("connection_geometry_mismatch", self._codes(model, path))

    def test_shifted_wall_overlaps_its_neighbour(self):
        model, path = self._joined("shift.ifc")
        f = ifcopenshell.open(str(path))
        wall = next(w for w in f.by_type("IfcWall") if w.Name == "W000")
        location = wall.ObjectPlacement.RelativePlacement.Location
        x, y, elevation = location.Coordinates
        location.Coordinates = (x+.2, y, elevation)
        f.write(str(path))
        self.assertIn("wall_overlap", self._codes(model, path))

    def test_missing_wall_is_reported_as_a_coverage_gap(self):
        model, path = self._joined("missing.ifc")
        f = ifcopenshell.open(str(path))
        wall = next(w for w in f.by_type("IfcWall") if w.Name == "W001")
        for inverse in list(f.get_inverse(wall)):
            f.remove(inverse)
        f.remove(wall)
        f.write(str(path))
        self.assertIn("wall_coverage_mismatch", self._codes(model, path))


if __name__=="__main__":
    unittest.main()
