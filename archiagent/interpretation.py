"""Versioned, source-bound construction snapshots; replay never invokes recognition.

The digest detects accidental changes, not reviewer authenticity. Frozen models
can be drafts: replay preserves assumptions and recomputes current validations.
Geometry is in model feet, just like BuildingModel; IFC authoring converts to SI.
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, MISSING, replace
from enum import Enum
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import types
from typing import get_args, get_origin, get_type_hints, Union, Literal

from archiagent.model import BuildingModel
from archiagent.validate import validate

SCHEMA_VERSION = 1
KIND = "archiagent.frozen-interpretation"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def implementation_versions():
    from archiagent.classify.prompt import PROMPT_VERSION
    from archiagent.classify.interpretation_prompt import INTERPRETATION_PROMPT_VERSION
    versions = {"python": platform.python_version(), "layer_prompt": PROMPT_VERSION,
                "interpretation_prompt": INTERPRETATION_PROMPT_VERSION}
    for package in ("archiagent", "ezdxf", "shapely", "ifcopenshell", "networkx", "numpy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    import shapely
    versions["geos"] = shapely.geos_version_string
    # Hash executable implementation, not just an editable install's version.
    root = Path(__file__).parent
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        h.update(path.relative_to(root).as_posix().encode()); h.update(b"\0")
        h.update(path.read_bytes()); h.update(b"\0")
    versions["implementation_sha256"] = h.hexdigest()
    return versions


def _decode(value, annotation, location):
    """Decode only statically declared model types; no classes from JSON input."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        for kind in args:
            try:
                return _decode(value, kind, location)
            except ValueError:
                pass
        raise ValueError(f"{location}: invalid value for union")
    if origin is Literal:
        if value not in args:
            raise ValueError(f"{location}: expected one of {args}")
        return value
    if annotation is type(None):
        if value is not None:
            raise ValueError(f"{location}: expected null")
        return None
    if annotation is bool:
        if type(value) is not bool:
            raise ValueError(f"{location}: expected boolean")
        return value
    if annotation in (float, int):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f"{location}: expected finite number")
        if annotation is int and type(value) is not int:
            raise ValueError(f"{location}: expected integer")
        return annotation(value)
    if annotation is str:
        if not isinstance(value, str):
            raise ValueError(f"{location}: expected string")
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{location}: invalid enum value") from exc
    if origin in (tuple, list):
        if not isinstance(value, list):
            raise ValueError(f"{location}: expected JSON array")
        repeated = len(args) == 2 and args[1] is Ellipsis
        if not repeated and len(value) != len(args):
            raise ValueError(f"{location}: expected {len(args)} entries")
        decoded = [_decode(v, args[0] if repeated else args[i], f"{location}[{i}]")
                   for i, v in enumerate(value)]
        return tuple(decoded) if origin is tuple else decoded
    if is_dataclass(annotation):
        if not isinstance(value, dict):
            raise ValueError(f"{location}: expected object")
        fs = {f.name: f for f in fields(annotation)}
        if extra := value.keys() - fs.keys():
            raise ValueError(f"{location}: unknown fields {sorted(extra)}")
        required = {f.name for f in fs.values() if f.default is MISSING and f.default_factory is MISSING}
        if missing := required - value.keys():
            raise ValueError(f"{location}: missing fields {sorted(missing)}")
        hints = get_type_hints(annotation)
        return annotation(**{key: _decode(val, hints[key], f"{location}.{key}")
                             for key, val in value.items()})
    raise ValueError(f"{location}: unsupported declared model type {annotation}")


def _decisions(models):
    records = []
    for index, model in enumerate(models):
        region = model.region_id or f"plan-{index + 1}"
        def add(subject, status, evidence, values, source_ids=()):
            records.append({"id": f"{region}/{subject}", "region_id": region,
                            "status": status, "evidence": evidence,
                            "source_ids": list(source_ids), "values": values,
                            "reviewer": {"kind": "pipeline", "human_approval": False}})
        add("registration", "accepted_by_rule", "Recorded extraction registration; not proof of floor identity",
            {"bounds_source": model.source_region_bounds, "origin_source": model.source_origin,
             "storey_name": model.storey_name, "elevation_ft": model.elevation_ft})
        add("scale", "accepted_by_rule" if model.scale_verified else "assumed_for_draft",
            model.scale.convention, asdict(model.scale))
        for i, wall in enumerate(model.walls):
            add(f"wall-{i}", "accepted_by_rule", wall.detector, asdict(wall), wall.source_ids)
        for profile in getattr(model, "wall_profiles", ()):
            add(f"profile-{profile.id}", profile.review_status, profile.detector,
                asdict(profile), profile.source_ids)
        for opening in model.openings:
            add(f"opening-{opening.id}", "assumed_for_draft" if opening.assumed_height else "accepted_by_rule",
                opening.evidence, asdict(opening), opening.source_ids)
        hosted = {op.symbol_id for op in model.openings}
        for symbol in model.symbols:
            status = "accepted_by_rule" if model.symbols_verified else "unreviewed_hypothesis"
            if symbol.kind in {"door", "window", "opening"} and symbol.id not in hosted:
                status = "unresolved"
            add(f"symbol-{symbol.id}", status, symbol.evidence, asdict(symbol), symbol.source_ids)
        for i, footprint in enumerate(model.footprints):
            add(f"footprint-{i}", "accepted_after_review" if model.footprint_verified else "assumed_for_draft",
                "Recorded floor boundary; verification flag preserved without inventing a reviewer", asdict(footprint))
        for i, assumption in enumerate(model.assumptions):
            add(f"assumption-{i}", "assumed_for_draft", assumption.reason, asdict(assumption))
    return records


def make_manifest(models, *, inputs=None):
    models = tuple(models)
    if not models:
        raise ValueError("cannot freeze an empty model collection")
    checksums = {m.source_sha256.lower() for m in models}
    if len(checksums) != 1:
        raise ValueError("one frozen interpretation must refer to one source checksum")
    checksum = next(iter(checksums))
    if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise ValueError("source checksum must be SHA256")
    payload = {"schema_version": SCHEMA_VERSION, "kind": KIND,
               "coordinate_system": "model-feet", "source_sha256": checksum,
               "versions": implementation_versions(), "models": [asdict(m) for m in models],
               "decisions": _decisions(models), "inputs": inputs or {},
               "unresolved": [{"region_id": m.region_id, **asdict(issue)}
                              for m in models for issue in m.issues if issue.severity in {"warn", "error"}],
               "accuracy": "unmeasured unless evaluated against independent source annotations",
               "replay_contract": "Replay uses models verbatim; decision records explain them. Digest is integrity, not approval."}
    payload["content_sha256"] = digest(payload)
    # Normalize enums and tuples before consumers persist or compare it.
    return json.loads(canonical_json(payload))


def save_manifest(models, path, *, inputs=None):
    manifest = make_manifest(models, inputs=inputs)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return path


def read_manifest(path, source_path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        data = json.loads(Path(path).read_text(), object_pairs_hook=unique_object,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"nonfinite JSON value {v}")))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid interpretation JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("kind") != KIND or type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported frozen interpretation schema")
    if data.get("coordinate_system") != "model-feet":
        raise ValueError("frozen geometry must use model-feet coordinates")
    allowed = {"schema_version", "kind", "coordinate_system", "source_sha256", "versions", "models",
               "decisions", "inputs", "unresolved", "accuracy", "replay_contract", "content_sha256"}
    if data.keys() - allowed or allowed - data.keys():
        raise ValueError("frozen interpretation has missing or unknown top-level fields")
    if not isinstance(data["versions"], dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                   for k, v in data["versions"].items()):
        raise ValueError("interpretation versions must be a string mapping")
    if not isinstance(data["inputs"], dict) or any(not isinstance(data[k], list) or
             any(not isinstance(v, dict) for v in data[k]) for k in ("decisions", "unresolved")):
        raise ValueError("invalid interpretation inputs or decision records")
    supplied = data.get("content_sha256")
    if supplied != digest({k: v for k, v in data.items() if k != "content_sha256"}):
        raise ValueError("interpretation content digest mismatch; regenerate a reviewed manifest revision")
    if data.get("source_sha256") != file_sha256(source_path):
        raise ValueError("interpretation source checksum does not match the supplied drawing")
    if not isinstance(data.get("models"), list) or not data["models"]:
        raise ValueError("interpretation requires at least one model")
    models = tuple(_decode(value, BuildingModel, f"models[{i}]") for i, value in enumerate(data["models"]))
    if any(m.source_sha256.lower() != data["source_sha256"] for m in models):
        raise ValueError("model source checksum differs from manifest source")
    keys = [m.region_id for m in models]
    if len(models) > 1 and (any(not k for k in keys) or len(set(keys)) != len(keys)):
        raise ValueError("replayed storeys require unique region IDs")
    for model in models:
        if not math.isfinite(model.scale.units_per_foot) or model.scale.units_per_foot <= 0:
            raise ValueError("frozen scale must be finite and positive")
    # Keep recorded warnings and add current gates, so deleting an issue cannot
    # turn an invalid model into an accepted one during replay.
    models = tuple(replace(m, issues=tuple(dict.fromkeys((*m.issues, *validate(m))))) for m in models)
    return models, data
