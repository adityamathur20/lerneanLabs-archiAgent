"""Stable IFC root identity from source/region and semantic element keys.

This guarantees replay identity, not byte-identical IFC serialization. Geometry
changes preserve identity when the frozen model's element keys are retained.
"""
from __future__ import annotations

import json
import uuid

import ifcopenshell.guid
import ifcopenshell.util.element

NAMESPACE = uuid.UUID("ecb8daf8-fafb-51e1-a6ed-a5792ed73d92")


def assign_stable_ids(f, models):
    source_scope = sorted((m.source_sha256, m.region_id) for m in models)
    namespace = uuid.uuid5(NAMESPACE, json.dumps(source_scope, separators=(",", ":")))
    keys = {}
    for product in f.by_type("IfcObjectDefinition"):
        props = ifcopenshell.util.element.get_pset(product, "ArchiAgent_Provenance") or {}
        # Absolute source/output paths must never alter model identity.
        # A wall keys as IfcWall whatever its subtype, so adopting
        # IfcWallStandardCase does not change an unchanged wall's GlobalId.
        ifc_class = "IfcWall" if product.is_a("IfcWall") else product.is_a()
        keys[product.id()] = (ifc_class, props.get("SourceSHA256", ""),
                              props.get("RegionId", ""), product.Name or "")
        if product.is_a() in {"IfcProject", "IfcSite", "IfcBuilding"}:
            keys[product.id()] = (product.is_a(), "singleton")
    for pset in f.by_type("IfcPropertySet"):
        owners = []
        for relation in f.get_inverse(pset):
            if relation.is_a("IfcRelDefinesByProperties"):
                owners.extend(keys[obj.id()] for obj in relation.RelatedObjects)
        keys[pset.id()] = ("IfcPropertySet", pset.Name, sorted(owners))

    def value_key(value):
        if isinstance(value, ifcopenshell.entity_instance):
            if value.id() in keys:
                return keys[value.id()]
            return (value.is_a(), [(k, value_key(v)) for k, v in value.get_info().items()
                                   if k not in {"id", "type", "GlobalId", "OwnerHistory"}])
        if isinstance(value, (tuple, list)):
            # Current relationship attributes are SETs, not ordered paths.
            return sorted((value_key(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
        return value

    seen = set()
    for root in f.by_type("IfcRoot"):
        key = value_key(root)
        encoded = json.dumps(key, sort_keys=True, separators=(",", ":"), allow_nan=False)
        guid = ifcopenshell.guid.compress(uuid.uuid5(namespace, encoded).hex)
        if guid in seen:
            raise ValueError(f"nonunique semantic IFC identity: {root.is_a()} {root.Name}")
        seen.add(guid)
        root.GlobalId = guid
