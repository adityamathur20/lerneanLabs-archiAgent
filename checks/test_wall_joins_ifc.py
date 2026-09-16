"""Authored walls are standard-case, parametric, and meet as drawn."""
import tempfile
import unittest
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element

from archiagent.ifc.author import author_ifc
from checks.test_wall_joins_fixtures import model_with

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


if __name__ == "__main__":
    unittest.main()
