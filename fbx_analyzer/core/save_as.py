"""Helpers for exporting modified FBX scenes without touching the original."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import SceneNode
from ..utils import resolve_enum_value
from . import sdk
from .validation import SceneValidator, round_trip_check
from .exceptions import FBXLoadError, FBXSaveError


@dataclass
class SceneExportDiagnostics:
    mode: str = "rebuild"
    reused_root_uid: Optional[int] = None
    source_child_count: int = 0
    created_nodes: List[Dict[str, Any]] = field(default_factory=list)
    reparented_nodes: List[Dict[str, Any]] = field(default_factory=list)
    removed_orphans: List[Dict[str, Any]] = field(default_factory=list)
    pruned_nodes: List[Dict[str, Any]] = field(default_factory=list)
    attribute_updates: List[Dict[str, Any]] = field(default_factory=list)
    transform_updates: List[Dict[str, Any]] = field(default_factory=list)
    validation_report_before: Dict[str, Any] = field(default_factory=dict)
    validation_report_after: Dict[str, Any] = field(default_factory=dict)
    auto_repairs: List[Dict[str, str]] = field(default_factory=list)
    roundtrip_report: Dict[str, Any] = field(default_factory=dict)

    def record_root_reuse(self, node) -> None:
        self.reused_root_uid = int(node.GetUniqueID())

    def record_creation(self, node, parent) -> None:
        self.created_nodes.append(
            {
                "node": self._node_summary(node),
                "parent": self._node_summary(parent) if parent is not None else None,
            }
        )

    def record_reparent(self, node, previous_parent, new_parent) -> None:
        self.reparented_nodes.append(
            {
                "node": self._node_summary(node),
                "previous_parent": self._node_summary(previous_parent)
                if previous_parent is not None
                else None,
                "new_parent": self._node_summary(new_parent) if new_parent is not None else None,
            }
        )

    def record_orphan_removal(self, node, parent) -> None:
        self.removed_orphans.append(
            {
                "node": self._node_summary(node),
                "parent": self._node_summary(parent) if parent is not None else None,
            }
        )

    def record_pruned(self, node) -> None:
        self.pruned_nodes.append(self._node_summary(node))

    def record_attribute(self, node, attr_type: str, attr_class: str) -> None:
        self.attribute_updates.append(
            {
                "node": self._node_summary(node),
                "attribute_type": attr_type,
                "attribute_class": attr_class,
            }
        )

    def record_transform(self, node, model: "SceneNode") -> None:
        self.transform_updates.append(
            {
                "node": self._node_summary(node),
                "translation": tuple(float(value) for value in model.translation),
                "rotation": tuple(float(value) for value in model.rotation),
                "scaling": tuple(float(value) for value in model.scaling),
            }
        )

    @staticmethod
    def _node_summary(node) -> Dict[str, Any]:
        return {
            "name": node.GetName() or "",
            "uid": int(node.GetUniqueID()),
        }

def save_scene_graph_as(
    source_path: str,
    target_path: str,
    scene_graph: Optional[SceneNode],
    diagnostics: Optional[SceneExportDiagnostics] = None,
    force_rebuild: bool = False,
) -> Optional[SceneExportDiagnostics]:
    """Persist edits in ``scene_graph`` into a copy of ``source_path``.

    When ``scene_graph`` is ``None`` and ``force_rebuild`` is false, the source file
    is copied verbatim. Set ``force_rebuild`` to ``True`` (optionally providing
    ``diagnostics``) to always round-trip the scene through the FBX SDK exporter,
    which is useful when debugging export discrepancies.

    Returns the ``SceneExportDiagnostics`` instance that was provided (or a new
    one if none was given)."""

    destination = str(target_path)
    if os.path.abspath(source_path) == os.path.abspath(destination):
        raise FBXSaveError("The destination path must be different from the source path.")

    Path(destination).parent.mkdir(parents=True, exist_ok=True)

    diagnostics = diagnostics or SceneExportDiagnostics()

    if scene_graph is None and not force_rebuild:
        diagnostics.mode = "copy"
        _copy_scene_file(source_path, destination)
        return diagnostics

    manager = sdk.create_manager()
    try:
        sdk.create_io_settings(manager)
        scene = sdk.create_scene(manager)
        if not sdk.load_scene(manager, scene, source_path):
            raise FBXLoadError(f"Failed to load FBX scene from '{source_path}'")

        scene_root = scene.GetRootNode()
        diagnostics.mode = "rebuild"
        diagnostics.source_child_count = scene_root.GetChildCount()
        diagnostics.record_root_reuse(scene_root)

        if scene_graph is not None:
            _apply_scene_graph_changes(scene, scene_graph, diagnostics)

        validator = SceneValidator(scene)
        validation_report = validator.validate()
        diagnostics.validation_report_before = validation_report.to_dict()

        baseline_metrics = validation_report.metrics

        if not validation_report.export_ready:
            validator.auto_repair(validation_report)
            diagnostics.auto_repairs = list(validation_report.repairs)
            post_repair_report = validator.validate()
            diagnostics.validation_report_after = post_repair_report.to_dict()
            baseline_metrics = post_repair_report.metrics
            if not post_repair_report.export_ready:
                summary = ", ".join(
                    f"{name}: {status}" for name, status in post_repair_report.status_summary().items()
                )
                raise FBXSaveError(
                    "Scene validation failed after auto-repair: "
                    f"{summary or 'unresolved issues present.'}"
                )
        else:
            diagnostics.validation_report_after = validation_report.to_dict()
            diagnostics.auto_repairs = []

        if not sdk.save_scene(manager, scene, destination):
            raise FBXSaveError(f"Failed to export FBX scene to '{destination}'")

        try:
            roundtrip = round_trip_check(
                destination,
                canonical_settings=validator.canonical,
                baseline_metrics=baseline_metrics,
            )
        except RuntimeError as exc:
            raise FBXSaveError(str(exc)) from exc
        diagnostics.roundtrip_report = roundtrip.to_dict()
        if not roundtrip.validation.export_ready or roundtrip.metrics_diff:
            summary = ", ".join(
                f"{name}: {status}" for name, status in roundtrip.validation.status_summary().items()
            )
            diff_summary = "; ".join(
                f"{entry['metric']} (expected {entry['expected']}, actual {entry['actual']})"
                for entry in roundtrip.metrics_diff
            )
            details = summary
            if diff_summary:
                details = f"{details}; metrics diff -> {diff_summary}" if details else diff_summary
            raise FBXSaveError(
                "Round-trip validation failed for exported scene: "
                f"{details or 'validation returned failures.'}"
            )
    finally:
        sdk.destroy_manager(manager)

    return diagnostics


def rebuild_scene_graph_as(
    source_path: str,
    target_path: str,
    scene_graph: Optional[SceneNode],
    *,
    diagnostics: Optional[SceneExportDiagnostics] = None,
) -> SceneExportDiagnostics:
    """Round-trip the source scene through the FBX SDK even when unmodified.

    This helper mirrors :func:`save_scene_graph_as` but always writes a fresh FBX
    file. It is intended for debugging export discrepancies: callers can provide
    a ``SceneExportDiagnostics`` instance to collect details about node
    creations, reparenting, and pruning that occurred during the rebuild.
    """

    result = save_scene_graph_as(
        source_path,
        target_path,
        scene_graph,
        diagnostics=diagnostics,
        force_rebuild=True,
    )
    if result is None:
        raise RuntimeError("Expected diagnostics from rebuild operation")
    return result


def _copy_scene_file(source_path: str, destination: str) -> None:
    """Copy the source scene verbatim when no edits were requested."""

    try:
        shutil.copy2(source_path, destination)
    except OSError as exc:  # pragma: no cover - depends on filesystem
        raise FBXSaveError(f"Failed to copy FBX file to '{destination}'") from exc


def _apply_scene_graph_changes(
    scene,
    scene_graph: SceneNode,
    diagnostics: Optional[SceneExportDiagnostics] = None,
) -> None:
    """Reconcile the editable ``SceneNode`` tree back onto the FBX scene."""

    scene_root = scene.GetRootNode()
    existing_nodes, existing_paths = _map_scene_nodes(scene_root)
    used_uids: set[int] = set()
    root_uid = scene_root.GetUniqueID()

    def sync(node_model: SceneNode, parent_fbx) -> Any:  # type: ignore[valid-type]
        fbx, _ = sdk.import_fbx_module()

        fbx_node = existing_nodes.get(node_model.uid) if node_model.uid is not None else None

        if fbx_node is None and node_model.parent_uid is None and parent_fbx is scene_root:
            # Treat the editable root node as the SDK scene root even if the UID does not match
            fbx_node = scene_root
            node_model.uid = root_uid
        elif fbx_node is None and node_model.uid is not None:
            fallback = existing_paths.get(node_model.original_path)
            if fallback is not None:
                fbx_node = fallback
                node_model.uid = fbx_node.GetUniqueID()

        if fbx_node is None:
            name = node_model.name or "Node"
            fbx_node = fbx.FbxNode.Create(scene, name)
            parent_fbx.AddChild(fbx_node)
            node_model.uid = fbx_node.GetUniqueID()
            existing_nodes[node_model.uid] = fbx_node
            if diagnostics is not None:
                diagnostics.record_creation(fbx_node, parent_fbx)
        else:
            previous_parent = fbx_node.GetParent()
            _ensure_parent(parent_fbx, fbx_node)
            if diagnostics is not None and previous_parent is not parent_fbx:
                diagnostics.record_reparent(fbx_node, previous_parent, parent_fbx)

        existing_nodes[node_model.uid] = fbx_node
        if node_model.original_path not in existing_paths:
            existing_paths[node_model.original_path] = fbx_node

        used_uids.add(fbx_node.GetUniqueID())

        _apply_node_attribute(
            scene,
            fbx_node,
            node_model.attribute_type,
            node_model.attribute_class,
            diagnostics,
        )
        _apply_node_transform(fbx_node, node_model, diagnostics)

        desired_children = [sync(child_model, fbx_node) for child_model in node_model.children]
        _remove_orphaned_children(fbx_node, desired_children, diagnostics)

        return fbx_node

    sync(scene_graph, scene_root)

    _prune_unused_nodes(scene_root, existing_nodes, used_uids, diagnostics)


def _map_scene_nodes(root) -> Tuple[Dict[int, Any], Dict[Tuple[int, ...], Any]]:  # type: ignore[valid-type]
    """Create UID and path lookups for every node in the current scene."""

    uid_mapping: Dict[int, Any] = {}
    path_mapping: Dict[Tuple[int, ...], Any] = {}

    def walk(node, path: Tuple[int, ...]) -> None:  # type: ignore[valid-type]
        uid_mapping[node.GetUniqueID()] = node
        path_mapping[path] = node
        for idx in range(node.GetChildCount()):
            walk(node.GetChild(idx), path + (idx,))

    walk(root, ())
    return uid_mapping, path_mapping


def _ensure_parent(parent_fbx, child):  # type: ignore[valid-type]
    """Ensure ``child`` is parented to ``parent_fbx``."""

    if child is parent_fbx:
        # The FBX root node is its own logical parent and must not be re-parented
        # or added as a child of itself. Attempting to do so results in an
        # additional root node being added under the true root on save.
        return

    current_parent = child.GetParent()
    if current_parent is parent_fbx:
        return
    if current_parent is not None:
        current_parent.RemoveChild(child)
    parent_fbx.AddChild(child)


def _remove_orphaned_children(
    parent, desired_children, diagnostics: Optional[SceneExportDiagnostics] = None
):  # type: ignore[valid-type]
    """Remove FBX children that are no longer represented in the model tree."""

    desired_ids = {child.GetUniqueID() for child in desired_children}
    for idx in reversed(range(parent.GetChildCount())):
        child = parent.GetChild(idx)
        if child.GetUniqueID() not in desired_ids:
            parent.RemoveChild(child)
            if diagnostics is not None:
                diagnostics.record_orphan_removal(child, parent)


def _prune_unused_nodes(
    scene_root,
    existing_nodes: Dict[int, Any],
    used_uids: set[int],
    diagnostics: Optional[SceneExportDiagnostics] = None,
) -> None:  # type: ignore[valid-type]
    """Delete nodes that were removed from the editable scene graph."""

    root_uid = scene_root.GetUniqueID()
    for uid, node in list(existing_nodes.items()):
        if uid == root_uid or uid in used_uids:
            continue
        parent = node.GetParent()
        if parent is None:
            continue
        children = [node.GetChild(i) for i in range(node.GetChildCount())]
        for child in children:
            node.RemoveChild(child)
            parent.AddChild(child)
            if diagnostics is not None:
                diagnostics.record_reparent(child, node, parent)
        if diagnostics is not None:
            diagnostics.record_pruned(node)
        parent.RemoveChild(node)


def _apply_node_attribute(
    scene, node, attr_type: str, attr_class: str, diagnostics: Optional[SceneExportDiagnostics] = None
) -> None:  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()
    skeleton_labels = {"Root": "eRoot", "Limb": "eLimb", "LimbNode": "eLimbNode", "Effector": "eEffector"}
    skeleton_types = {}
    for label, enum_name in skeleton_labels.items():
        try:
            skeleton_types[label] = resolve_enum_value(fbx.FbxSkeleton, enum_name)
        except AttributeError:
            continue

    node_attribute = node.GetNodeAttribute()

    if attr_type in skeleton_types and skeleton_types[attr_type] is not None:
        if not isinstance(node_attribute, fbx.FbxSkeleton):
            skeleton = fbx.FbxSkeleton.Create(scene, node.GetName() or "Skeleton")
            node.SetNodeAttribute(skeleton)
            node_attribute = skeleton
        node_attribute.SetSkeletonType(skeleton_types[attr_type])
    else:
        if isinstance(node_attribute, fbx.FbxSkeleton) and attr_type == "Node":
            node.SetNodeAttribute(None)

    if diagnostics is not None:
        diagnostics.record_attribute(node, attr_type, attr_class)


def _apply_node_transform(
    node, model: SceneNode, diagnostics: Optional[SceneExportDiagnostics] = None
) -> None:  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()

    translation = fbx.FbxDouble3(*model.translation)
    rotation = fbx.FbxDouble3(*model.rotation)
    scaling = fbx.FbxDouble3(*model.scaling)

    node.LclTranslation.Set(translation)
    node.LclRotation.Set(rotation)
    node.LclScaling.Set(scaling)

    if diagnostics is not None:
        diagnostics.record_transform(node, model)







