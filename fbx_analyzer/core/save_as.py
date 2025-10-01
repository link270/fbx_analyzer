"""Helpers for exporting modified FBX scenes without touching the original."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from ..models import SceneNode
from ..utils import resolve_enum_value
from . import sdk
from .exceptions import FBXLoadError, FBXSaveError


def save_scene_graph_as(source_path: str, target_path: str, scene_graph: Optional[SceneNode]) -> None:
    """Persist edits in ``scene_graph`` into a copy of ``source_path``."""

    destination = str(target_path)
    if os.path.abspath(source_path) == os.path.abspath(destination):
        raise FBXSaveError("The destination path must be different from the source path.")

    Path(destination).parent.mkdir(parents=True, exist_ok=True)

    if scene_graph is None:
        _copy_scene_file(source_path, destination)
        return

    manager = sdk.create_manager()
    try:
        sdk.create_io_settings(manager)
        scene = sdk.create_scene(manager)
        if not sdk.load_scene(manager, scene, source_path):
            raise FBXLoadError(f"Failed to load FBX scene from '{source_path}'")

        _apply_scene_graph_changes(scene, scene_graph)

        if not sdk.save_scene(manager, scene, destination):
            raise FBXSaveError(f"Failed to export FBX scene to '{destination}'")
    finally:
        sdk.destroy_manager(manager)


def _copy_scene_file(source_path: str, destination: str) -> None:
    """Copy the source scene verbatim when no edits were requested."""

    try:
        shutil.copy2(source_path, destination)
    except OSError as exc:  # pragma: no cover - depends on filesystem
        raise FBXSaveError(f"Failed to copy FBX file to '{destination}'") from exc


def _apply_scene_graph_changes(scene, scene_graph: SceneNode) -> None:
    """Reconcile the editable ``SceneNode`` tree back onto the FBX scene."""

    scene_root = scene.GetRootNode()
    existing_nodes = _map_scene_nodes(scene_root)
    used_uids: set[int] = set()

    def sync(node_model: SceneNode, parent_fbx) -> Any:  # type: ignore[valid-type]
        fbx, _ = sdk.import_fbx_module()

        fbx_node = existing_nodes.get(node_model.uid) if node_model.uid is not None else None

        if fbx_node is None:
            name = node_model.name or "Node"
            fbx_node = fbx.FbxNode.Create(scene, name)
            parent_fbx.AddChild(fbx_node)
            node_model.uid = fbx_node.GetUniqueID()
            existing_nodes[node_model.uid] = fbx_node
        else:
            _ensure_parent(parent_fbx, fbx_node)

        used_uids.add(fbx_node.GetUniqueID())

        _apply_node_attribute(scene, fbx_node, node_model.attribute_type, node_model.attribute_class)
        _apply_node_transform(fbx_node, node_model)

        desired_children = [sync(child_model, fbx_node) for child_model in node_model.children]
        _remove_orphaned_children(fbx_node, desired_children)

        return fbx_node

    sync(scene_graph, scene_root)

    _prune_unused_nodes(scene_root, existing_nodes, used_uids)


def _map_scene_nodes(root) -> Dict[int, Any]:  # type: ignore[valid-type]
    """Create a UID lookup for every node in the current scene."""

    mapping: Dict[int, Any] = {}

    def walk(node) -> None:  # type: ignore[valid-type]
        mapping[node.GetUniqueID()] = node
        for idx in range(node.GetChildCount()):
            walk(node.GetChild(idx))

    walk(root)
    return mapping


def _ensure_parent(parent_fbx, child):  # type: ignore[valid-type]
    """Ensure ``child`` is parented to ``parent_fbx``."""

    current_parent = child.GetParent()
    if current_parent is parent_fbx:
        return
    if current_parent is not None:
        current_parent.RemoveChild(child)
    parent_fbx.AddChild(child)


def _remove_orphaned_children(parent, desired_children):  # type: ignore[valid-type]
    """Remove FBX children that are no longer represented in the model tree."""

    desired_ids = {child.GetUniqueID() for child in desired_children}
    for idx in reversed(range(parent.GetChildCount())):
        child = parent.GetChild(idx)
        if child.GetUniqueID() not in desired_ids:
            parent.RemoveChild(child)


def _prune_unused_nodes(scene_root, existing_nodes: Dict[int, Any], used_uids: set[int]) -> None:  # type: ignore[valid-type]
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
        parent.RemoveChild(node)


def _apply_node_attribute(scene, node, attr_type: str, attr_class: str) -> None:  # type: ignore[valid-type]
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


def _apply_node_transform(node, model: SceneNode) -> None:  # type: ignore[valid-type]
    node.LclTranslation.Set(*model.translation)
    node.LclRotation.Set(*model.rotation)
    node.LclScaling.Set(*model.scaling)
