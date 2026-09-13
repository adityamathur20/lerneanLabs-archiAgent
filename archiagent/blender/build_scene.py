"""Standalone Blender entrypoint for a package produced by bridge.py.

Run inside Blender; the source IFC is never rewritten. Existing scenes are
preserved. Bonsai linkage is optional and only safe in a session without an
already loaded IFC project. No plugin install or network request is made.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


def build(manifest_path, output_path, *, link_bonsai=False, presentation=True,
          overwrite=False):
    import bpy
    from mathutils import Vector

    manifest_path = Path(manifest_path).resolve()
    output_path = Path(output_path).resolve()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to replace existing Blender file: {output_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "archiagent.ifc-meshes.v1":
        raise ValueError("Unsupported mesh manifest format")
    if manifest.get("coordinate_system") != "world_metres":
        raise ValueError("Expected world-space geometry in metres")
    ifc_path = Path(manifest["source_ifc"])
    if not ifc_path.is_absolute():
        ifc_path = manifest_path.parent / ifc_path
    if hashlib.sha256(ifc_path.read_bytes()).hexdigest() != manifest["source_sha256"]:
        raise ValueError("Source IFC checksum does not match the mesh manifest")

    ifc_tool = ifc_file = None
    if link_bonsai:
        # Factory-startup sessions can see an installed Bonsai package without
        # having registered its Blender properties. Enable it for this process
        # only; never install it or persist a user preference change.
        if not hasattr(bpy.types.Scene, "BIMProperties"):
            import addon_utils
            # Bonsai needs an in-memory AddonPreferences entry (default_set).
            # No save_userpref operation is called, so this is not persisted.
            if addon_utils.enable("bonsai", default_set=True, persistent=False) is None:
                raise RuntimeError("Installed Bonsai could not be enabled in this session")
        import bonsai.tool as tool
        import ifcopenshell
        if tool.Ifc.get() is not None:
            raise RuntimeError("Bonsai already has an IFC loaded; use a fresh Blender session")
        ifc_tool = tool.Ifc
        ifc_file = ifcopenshell.open(str(ifc_path))

    scene = bpy.data.scenes.new("ArchiAgent | IFC Building")
    if bpy.context.window is not None:
        bpy.context.window.scene = scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene["source_ifc"] = str(ifc_path)
    scene["source_sha256"] = manifest["source_sha256"]
    scene["ifc_linkage"] = "Bonsai" if link_bonsai else "identity metadata only"
    if link_bonsai:
        ifc_tool.set(ifc_file)
        props = getattr(scene, "BIMProperties", None)
        if props is None:
            props = tool.Blend.get_bim_props()
        props.ifc_file = str(ifc_path)

    collections, material_cache, objects = {}, {}, []

    def material(style):
        key = json.dumps(style, sort_keys=True)
        if key not in material_cache:
            mat = bpy.data.materials.new(style["name"] or "IFC surface")
            alpha = 1.0 - style["transparency"]
            rgba = (*style["color"], alpha)
            mat.diffuse_color = rgba
            mat.use_nodes = True
            shader = mat.node_tree.nodes.get("Principled BSDF")
            shader.inputs["Base Color"].default_value = rgba
            shader.inputs["Alpha"].default_value = alpha
            shader.inputs["Roughness"].default_value = .18 if alpha < 1 else .65
            transmission = shader.inputs.get("Transmission Weight") or shader.inputs.get("Transmission")
            if transmission is not None:
                transmission.default_value = style["transparency"]
            material_cache[key] = mat
        return material_cache[key]

    default = {"name": "Unspecified IFC appearance", "color": [.7, .7, .7],
               "transparency": 0.0}
    for element in manifest["elements"]:
        group_key = element["container_guid"] or "uncontained"
        if group_key not in collections:
            collection = bpy.data.collections.new(element["storey"] or "Unnamed container")
            collection["ifc_container_guid"] = group_key
            scene.collection.children.link(collection)
            collections[group_key] = collection
        verts = element["vertices"]
        faces = element["faces"]
        mesh = bpy.data.meshes.new(f'{element["class"]} | {element["guid"]}')
        mesh.from_pydata([verts[i:i+3] for i in range(0, len(verts), 3)], [],
                         [faces[i:i+3] for i in range(0, len(faces), 3)])
        mesh.update()
        obj = bpy.data.objects.new(f'{element["class"]} | {element["name"]}', mesh)
        collections[group_key].objects.link(obj)
        obj["ifc_global_id"] = element["guid"]
        obj["ifc_step_id"] = element["id"]
        obj["ifc_class"] = element["class"]
        obj["source_ifc_sha256"] = manifest["source_sha256"]
        for style in element["materials"]:
            mesh.materials.append(material(style))
        if -1 in element["material_ids"] or not element["materials"]:
            mesh.materials.append(material(default))
        for polygon, index in zip(mesh.polygons, element["material_ids"]):
            polygon.material_index = len(element["materials"]) if index == -1 else index
        if ifc_tool is not None:
            ifc_tool.link(ifc_file.by_id(element["id"]), obj)
        objects.append(obj)

    if ifc_tool is not None:
        hierarchy = bpy.data.collections.new("IFC spatial hierarchy")
        scene.collection.children.link(hierarchy)
        for class_name in ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey"):
            for entity in sorted(ifc_file.by_type(class_name), key=lambda e: e.GlobalId):
                obj = bpy.data.objects.new(f"{class_name} | {entity.Name or ''}", None)
                hierarchy.objects.link(obj)
                obj["ifc_global_id"] = entity.GlobalId
                obj.hide_render = True
                ifc_tool.link(entity, obj)

    if presentation and objects:
        points = [Vector(vertex) for obj in objects for vertex in obj.bound_box]
        minimum = Vector(tuple(min(p[i] for p in points) for i in range(3)))
        maximum = Vector(tuple(max(p[i] for p in points) for i in range(3)))
        center = (minimum + maximum) / 2
        span = max((maximum - minimum).length, 1.0)
        camera_data = bpy.data.cameras.new("ArchiAgent Camera")
        camera = bpy.data.objects.new("ArchiAgent Camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = center + Vector((1, -1, .85)).normalized() * span * 2
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = span * 1.2
        camera_data.clip_end = span * 10
        scene.camera = camera
        sun_data = bpy.data.lights.new("ArchiAgent Sun", "SUN")
        sun_data.energy = 2.0
        sun_data.angle = math.radians(15)
        sun = bpy.data.objects.new("ArchiAgent Sun", sun_data)
        scene.collection.objects.link(sun)
        sun.rotation_euler = (.4, -.6, -.4)
        scene.world = bpy.data.worlds.new("ArchiAgent World")
        scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.3, .3, .3, 1)
        scene.render.resolution_x = 1600
        scene.render.resolution_y = 1600
        scene.render.resolution_percentage = 100

    notes = bpy.data.texts.new("ArchiAgent model notes")
    notes.write("\n".join(manifest["limitations"]) + "\n"
                "Source IFC: " + str(ifc_path) + "\n"
                "Bonsai linkage: " + str(link_bonsai) + "\n"
                "Use Bonsai editing/export tools for BIM changes. An ordinary mesh edit "
                "does not update source.ifc. Round-trip editing was not tested by this script.\n")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    return {"objects": len(objects), "scene": scene.name, "output": str(output_path),
            "bonsai_linked": link_bonsai}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--link-bonsai", action="store_true")
    parser.add_argument("--no-presentation", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    print(json.dumps(build(args.manifest, args.output, link_bonsai=args.link_bonsai,
                           presentation=not args.no_presentation, overwrite=args.overwrite)))


if __name__ == "__main__":
    main()
