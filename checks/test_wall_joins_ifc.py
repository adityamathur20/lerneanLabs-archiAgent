"""Authored walls are standard-case, parametric, and meet as drawn."""
import tempfile
import unittest
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element
from shapely.geometry import box
from shapely.ops import unary_union

from archiagent.ifc.author import author_ifc
from checks.test_wall_joins_fixtures import footprint, model_with

FT = .3048


def author(segments, **kwargs):
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "joins.ifc"
    author_ifc(model_with(segments, **kwargs), path)
    return ifcopenshell.open(str(path)), directory


class WallJoinAuthoringChecks(unittest.TestCase):
    def test_runs_are_standard_case_with_axis_and_centred_layer(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (10, 0), .5)])
        self.addCleanup(directory.cleanup)
        walls = f.by_type("IfcWall")
        self.assertEqual([w.Name for w in walls], ["W000"])
        self.assertEqual(walls[0].is_a(), "IfcWallStandardCase")
        identifiers = {r.RepresentationIdentifier for r in walls[0].Representation.Representations}
        self.assertEqual(identifiers, {"Body", "Axis"})
        usage = ifcopenshell.util.element.get_material(walls[0])
        self.assertEqual(usage.is_a(), "IfcMaterialLayerSetUsage")
        self.assertAlmostEqual(usage.OffsetFromReferenceLine, -.25 * FT)
        self.assertEqual([l.LayerThickness for l in usage.ForLayerSet.MaterialLayers], [.5 * FT])
        pset = ifcopenshell.util.element.get_psets(walls[0])["ArchiAgent_Provenance"]
        self.assertEqual(pset["ModelWallIndices"], "[0, 1]")

    def test_body_is_a_rectangle_profile(self):
        f, directory = author([((0, 0), (5, 0), .5)])
        self.addCleanup(directory.cleanup)
        body = next(r for r in f.by_type("IfcWall")[0].Representation.Representations
                    if r.RepresentationIdentifier == "Body")
        profile = body.Items[0].SweptArea
        self.assertEqual(profile.is_a(), "IfcRectangleProfileDef")
        self.assertAlmostEqual(profile.XDim, 5 * FT)
        self.assertAlmostEqual(profile.YDim, .5 * FT)

    def test_stable_ids_ignore_the_standard_case_subtype(self):
        """An unmerged wall must keep the GlobalId it had as a plain IfcWall."""
        import ifcopenshell.guid

        from archiagent.ifc.identity import assign_stable_ids

        model = model_with([((0, 0), (5, 0), .5)])
        ids = []
        for ifc_class in ("IfcWall", "IfcWallStandardCase"):
            f = ifcopenshell.file(schema="IFC4")
            product = f.create_entity(ifc_class, GlobalId=ifcopenshell.guid.new(), Name="W000")
            pset = f.create_entity(
                "IfcPropertySet", GlobalId=ifcopenshell.guid.new(), Name="ArchiAgent_Provenance",
                HasProperties=[
                    f.create_entity("IfcPropertySingleValue", Name="SourceSHA256",
                                    NominalValue=f.create_entity("IfcText", "b" * 64)),
                    f.create_entity("IfcPropertySingleValue", Name="RegionId",
                                    NominalValue=f.create_entity("IfcText", "plan-a"))])
            f.create_entity("IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(),
                            RelatedObjects=[product], RelatingPropertyDefinition=pset)
            assign_stable_ids(f, (model,))
            ids.append(product.GlobalId)
        self.assertEqual(ids[0], ids[1])


class WallJointGeometryChecks(unittest.TestCase):
    """The reviewed sketch: one wall owns the intersection, nothing overlaps."""

    def assert_matches_sketch(self, f, expected):
        shapes = [footprint(w) for w in f.by_type("IfcWall")]
        overlap = sum(a.intersection(b).area for i, a in enumerate(shapes) for b in shapes[i+1:])
        self.assertLess(overlap, 1e-9, "walls must not overlap")
        self.assertLess(unary_union(shapes).symmetric_difference(expected).area, 1e-9)

    def test_l_corner_is_butted_with_the_thicker_wall_owning_it(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        self.addCleanup(directory.cleanup)
        self.assert_matches_sketch(f, unary_union([
            box(0, -.25, 5-.375, .25), box(5-.375, -4, 5+.375, .25)]))

    def test_t_stem_stops_at_the_through_wall_face(self):
        f, directory = author([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                               ((5, 0), (5, -4), .5)])
        self.addCleanup(directory.cleanup)
        self.assert_matches_sketch(f, unary_union([
            box(0, -.375, 10, .375), box(5-.25, -4, 5+.25, -.375)]))

    def test_x_splits_the_thinner_wall_around_the_thicker_one(self):
        f, directory = author([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                               ((5, -4), (5, 0), .5), ((5, 0), (5, 4), .5)])
        self.addCleanup(directory.cleanup)
        self.assert_matches_sketch(f, unary_union([
            box(0, -.375, 10, .375), box(5-.25, -4, 5+.25, -.375), box(5-.25, .375, 5+.25, 4)]))


class WallJointProvenanceChecks(unittest.TestCase):
    """Joined walls stay parametric, and what could not be joined is reported."""

    def storey_provenance(self, f):
        return ifcopenshell.util.element.get_psets(
            f.by_type("IfcBuildingStorey")[0])["ArchiAgent_Provenance"]

    def test_joined_walls_keep_rectangle_profiles_and_report_exceptions(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)])
        self.addCleanup(directory.cleanup)
        for wall in f.by_type("IfcWall"):
            body = next(r for r in wall.Representation.Representations
                        if r.RepresentationIdentifier == "Body")
            self.assertEqual(body.Items[0].SweptArea.is_a(), "IfcRectangleProfileDef")
        storey = self.storey_provenance(f)
        self.assertEqual(storey["JointGeometry"], "Butt joints; intersection owned by one wall")
        self.assertEqual(storey["NonRectangularWallProfiles"], 0)
        self.assertEqual(storey["UntrimmedJunctionsJSON"], "[]")

    def test_angled_joint_keeps_a_polygon_and_is_counted(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (8, 3), .5)])
        self.addCleanup(directory.cleanup)
        self.assertGreater(self.storey_provenance(f)["NonRectangularWallProfiles"], 0)

    def test_a_thickness_step_is_reported_as_an_untrimmed_junction(self):
        f, directory = author([((0, 0), (5, 0), .5), ((5, 0), (9, 0), .75)])
        self.addCleanup(directory.cleanup)
        self.assertEqual(self.storey_provenance(f)["UntrimmedJunctionsJSON"], "[[5.0, 0.0]]")

    def test_every_connection_carries_its_junction_point(self):
        import ifcopenshell.util.placement as placement
        import numpy

        f, directory = author([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                               ((5, 0), (5, -4), .5)])
        self.addCleanup(directory.cleanup)
        relationships = f.by_type("IfcRelConnectsPathElements")
        self.assertEqual(len(relationships), 1)
        for relationship in relationships:
            geometry = relationship.ConnectionGeometry
            self.assertEqual(geometry.is_a(), "IfcConnectionPointGeometry")
            for element, point in ((relationship.RelatingElement, geometry.PointOnRelatingElement),
                                   (relationship.RelatedElement, geometry.PointOnRelatedElement)):
                matrix = placement.get_local_placement(element.ObjectPlacement)
                values = list(point.Coordinates) + [0., 0., 0.]
                world = (matrix @ numpy.array([values[0], values[1], values[2], 1.]))[:2] / FT
                self.assertAlmostEqual(world[0], 5, places=6)
                self.assertAlmostEqual(world[1], 0, places=6)

    def test_voids_styles_and_profile_walls_survive_regeneration(self):
        import ifcopenshell.validate

        from archiagent.geometry.profiles import WallProfile
        from archiagent.semantic import Opening, SymbolInstance

        profile = WallProfile("wp-test", ((6, 2), (6, 6), (6.5, 6), (6.5, 3), (8, 3), (8, 2), (6, 2)),
                              (), "walls", ("cad:p",), "native-wall-face", "accepted_by_rule")
        f, directory = author(
            [((0, 0), (5, 0), .5), ((5, 0), (5, -4), .75)],
            symbols=(SymbolInstance("d1", "door", (2.5, 0.), 2., .5, source_ids=("cad:d",),
                                    evidence="block", confidence=1.),),
            openings=(Opening("o1", "door", 0, (1.5, 0.), (3.5, 0.), 7., symbol_id="d1",
                              assumed_height=False),),
            wall_profiles=(profile,))
        self.addCleanup(directory.cleanup)
        host = f.by_type("IfcOpeningElement")[0].VoidsElements[0].RelatingBuildingElement
        self.assertEqual(host.Name, "W000")
        outline_wall = next(w for w in f.by_type("IfcWall") if w.Name.startswith("WP:"))
        self.assertEqual(outline_wall.is_a(), "IfcWall")
        styled = {item.Item for item in f.by_type("IfcStyledItem")}
        for wall in f.by_type("IfcWall"):
            body = next(r for r in wall.Representation.Representations
                        if r.RepresentationIdentifier == "Body")
            self.assertIn(body.Items[0], styled)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(f, logger, express_rules=True)
        self.assertEqual([s for s in logger.statements
                          if str(s.get("level", "")).lower() in {"error", "critical"}], [])


class WallJoinRobustnessChecks(unittest.TestCase):
    """What the sample drawings exposed that the square fixtures did not."""

    def test_oblique_corner_matches_its_prediction(self):
        from archiagent.ifc.inspect import validate_export

        # SANJANA SURESH JI: walls meet at ~50 degrees. The owning wall reaches
        # the far face of the wall stopping there, which is thickness/2 divided
        # by sin(angle), and its end is cut on a slant rather than square.
        model = model_with([((0, 0), (5, 0), .375), ((5, 0), (8, 3.6), .375)])
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)/"oblique.ifc"
        author_ifc(model, path)
        report = validate_export(path, (model,))
        self.assertTrue(report["passed"], report["errors"])

    def test_material_layer_priorities_stay_within_the_ifc_range(self):
        # VINAYAK APARTMENTS has 802 runs; IFC allows a layer priority of 0-100.
        segments = [((i*2., 0.), (i*2.+1.5, 0.), .375) for i in range(60)]
        segments += [((i*2., 10.), (i*2.+1.5, 10.), .5) for i in range(60)]
        f, directory = author(segments)
        self.addCleanup(directory.cleanup)
        priorities = [layer.Priority for layer in f.by_type("IfcMaterialLayer")]
        self.assertTrue(priorities)
        self.assertTrue(all(0 <= p <= 100 for p in priorities), sorted(priorities)[-5:])

    def test_a_near_rectangular_outline_is_still_written_as_a_rectangle(self):
        from archiagent.ifc.author import _restore_rectangle

        # Aiims Road: a regenerated outline misses its bounding box by ~2e-12
        # square metres on real coordinates. That is a rectangle, and the
        # checker treats it as one, so authoring must agree.
        f, directory = author([((0, 0), (5, 0), .5)])
        self.addCleanup(directory.cleanup)
        wall = f.by_type("IfcWall")[0]
        item = next(r for r in wall.Representation.Representations
                    if r.RepresentationIdentifier == "Body").Items[0]
        rectangle = item.SweptArea
        x, y = rectangle.Position.Location.Coordinates if rectangle.Position else (0., 0.)
        hx, hy = rectangle.XDim/2, rectangle.YDim/2
        drift = 2e-12/(2*hy)  # shifts the area by ~2e-12 m2, as the drawings do
        ring = [(x-hx, y-hy), (x+hx, y-hy), (x+hx+drift, y+hy), (x-hx, y+hy), (x-hx, y-hy)]
        item.SweptArea = f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA",
            OuterCurve=f.createIfcPolyline([f.createIfcCartesianPoint(p) for p in ring]))
        self.assertTrue(_restore_rectangle(f, wall))
        self.assertEqual(
            next(r for r in wall.Representation.Representations
                 if r.RepresentationIdentifier == "Body").Items[0].SweptArea.is_a(),
            "IfcRectangleProfileDef")

    def test_a_door_spanning_a_trimmed_wall_only_removes_material_that_exists(self):
        from archiagent.ifc.inspect import validate_export
        from archiagent.semantic import Opening, SymbolInstance

        # MR RAJEEV JI TWANI JI W167: a doorway host is trimmed where it stops
        # against another wall, so the part of the void past the trim removes
        # nothing. Subtracting it over the untrimmed length loses ~19% volume.
        model = model_with(
            [((0, 0), (0, -4), .375), ((0, 0), (2.9375, 0), .375)],
            symbols=(SymbolInstance("d1", "door", (1.46875, 0.), 2.9375, .375,
                                    source_ids=("cad:d",), evidence="block", confidence=1.),),
            openings=(Opening("o1", "door", 1, (0., 0.), (2.9375, 0.), 7.,
                              symbol_id="d1", assumed_height=False),))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)/"lintel.ifc"
        author_ifc(model, path)
        report = validate_export(path, (model,))
        self.assertTrue(report["passed"], report["errors"])

    def test_a_long_stem_does_not_notch_its_through_wall(self):
        # The stem's line is longer than the through wall's, so ranking walls
        # globally would let the stem outrank the wall it must stop at.
        f, directory = author([((0, 0), (2, 0), .375), ((2, 0), (4, 0), .375),
                               ((2, 0), (2, -20), .375)])
        self.addCleanup(directory.cleanup)
        through = next(w for w in f.by_type("IfcWall") if w.Name == "W000")
        body = next(r for r in through.Representation.Representations
                    if r.RepresentationIdentifier == "Body")
        self.assertEqual(body.Items[0].SweptArea.is_a(), "IfcRectangleProfileDef")
        self.assertAlmostEqual(footprint(through).area, 4*.375, places=6)


if __name__ == "__main__":
    unittest.main()
