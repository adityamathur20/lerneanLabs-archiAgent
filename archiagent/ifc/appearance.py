"""Reusable illustrative IFC surface styles, never verified material assemblies.

These defaults only communicate element types visually. They do not establish
construction layers, finish specifications, structural or thermal properties.
Styles are attached to representation items and survive IFC tessellation.
"""
from __future__ import annotations


PRESENTATION_PALETTE = {
    "IfcWall": ("Plaster", (.81, .79, .72), 0.0),
    "IfcSlab": ("Floor", (.66, .61, .51), 0.0),
    "IfcColumn": ("Concrete", (.49, .53, .55), 0.0),
    "IfcBeam": ("Concrete", (.49, .53, .55), 0.0),
    "IfcDoor": ("Wood", (.36, .20, .09), 0.0),
    "IfcWindow": ("Simplified glazing", (.34, .65, .72), .58),
}
APPEARANCE_BASIS = "Illustrative class palette; not a verified material or construction assembly"


class PresentationStyles:
    """One cache per IFC file, shared by all storeys and physical elements."""

    def __init__(self, f):
        self.file = f
        self.styles = {}

    def assign(self, ifc_class, item):
        preset = PRESENTATION_PALETTE.get(ifc_class)
        if preset is None:
            return
        label, rgb, transparency = preset
        if preset not in self.styles:
            f = self.file
            colour = f.create_entity("IfcColourRgb", Name=None, Red=rgb[0], Green=rgb[1], Blue=rgb[2])
            shading = f.create_entity("IfcSurfaceStyleShading", SurfaceColour=colour, Transparency=transparency)
            self.styles[preset] = f.create_entity("IfcSurfaceStyle", Name=f"Illustrative {label}",
                                                 Side="BOTH", Styles=[shading])
        self.file.create_entity("IfcStyledItem", Item=item, Styles=[self.styles[preset]], Name=None)
