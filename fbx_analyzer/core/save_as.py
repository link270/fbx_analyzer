"""Helpers for exporting modified FBX scenes without touching the original."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import SceneNode
from . import sdk
from .exceptions import FBXLoadError, FBXSaveError


def save_scene_graph_as(source_path: str, target_path: str, scene_graph: Optional[SceneNode]) -> None:
    """Persist ``scene_graph`` edits into a copy of ``source_path``.

    The original FBX file is never modified. All changes are written to the
    file located at ``target_path``.
    """

    target_path = str(target_path)
    if os.path.abspath(source_path) == os.path.abspath(target_path):
        raise FBXSaveError("The destination path must be different from the source path.")

    target_dir = Path(target_path).parent
    target_dir.mkdir(parents=True, exist_ok=True)

    if scene_graph is None:
        try:
            shutil.copy2(source_path, target_path)
        except OSError as exc:  # pragma: no cover - depends on filesystem state
            raise FBXSaveError(f"Failed to copy FBX file to '{target_path}'") from exc
        return

    manager = sdk.create_manager()
    try:
        sdk.create_io_settings(manager)
        scene = sdk.create_scene(manager)
        if not sdk.load_scene(manager, scene, source_path):
            raise FBXLoadError(f"Failed to load FBX scene from '{source_path}'")

        _apply_scene_graph_changes(scene, scene_graph)

        if not sdk.save_scene(manager, scene, target_path):
            raise FBXSaveError(f"Failed to export FBX scene to '{target_path}'")
    finally:
        sdk.destroy_manager(manager)


def _apply_scene_graph_changes(scene, scene_graph: SceneNode) -> None:
    nodes_by_uid = _map_scene_nodes(scene.GetRootNode())
    desired_relations, desired_attributes = _collect_desired_state(scene_graph)

    _reparent_nodes(scene.GetRootNode(), nodes_by_uid, desired_relations, scene_graph)
    _remove_deleted_nodes(scene.GetRootNode(), nodes_by_uid, desired_relations)
    _apply_attribute_types(scene, nodes_by_uid, desired_attributes)


def _map_scene_nodes(root) -> Dict[int, Any]:  # type: ignore[valid-type]
    mapping: Dict[int, Any] = {}  # type: ignore[valid-type]

    def walk(node) -> None:  # type: ignore[valid-type]
        mapping[node.GetUniqueID()] = node
        for idx in range(node.GetChildCount()):
            walk(node.GetChild(idx))

    walk(root)
    return mapping


def _collect_desired_state(scene_graph: SceneNode) -> Tuple[Dict[int, Optional[int]], Dict[int, Tuple[str, str]]]:
    relations: Dict[int, Optional[int]] = {}
    attributes: Dict[int, Tuple[str, str]] = {}

    def visit(node: SceneNode, parent_uid: Optional[int]) -> None:
        if node.uid is None:
            return
        relations[node.uid] = parent_uid
        attributes[node.uid] = (node.attribute_type, node.attribute_class)
        for child in node.children:
            visit(child, node.uid)

    visit(scene_graph, None)
    return relations, attributes


def _reparent_nodes(scene_root, nodes_by_uid: Dict[int, Any], relations: Dict[int, Optional[int]], scene_graph: SceneNode) -> None:  # type: ignore[valid-type]
    ordered_uids: List[int] = []

    def collect(node: SceneNode) -> None:
        if node.uid is not None:
            ordered_uids.append(node.uid)
        for child in node.children:
            collect(child)

    collect(scene_graph)

    for uid in ordered_uids:
        node = nodes_by_uid.get(uid)
        if node is None:
            continue
        desired_parent_uid = relations.get(uid)
        current_parent = node.GetParent()
        current_parent_uid = current_parent.GetUniqueID() if current_parent else None
        if current_parent_uid == desired_parent_uid:
            continue
        if desired_parent_uid is None:
            if current_parent is None:
                continue
            current_parent.RemoveChild(node)
            scene_root.AddChild(node)
            continue
        desired_parent = nodes_by_uid.get(desired_parent_uid)
        if desired_parent is None:
            continue
        if current_parent is not None:
            current_parent.RemoveChild(node)
        desired_parent.AddChild(node)


def _remove_deleted_nodes(scene_root, nodes_by_uid: Dict[int, Any], relations: Dict[int, Optional[int]]) -> None:  # type: ignore[valid-type]
    desired_uids = set(relations.keys())
    for uid, node in list(nodes_by_uid.items()):
        if uid in desired_uids:
            continue
        if node is scene_root:
            continue
        parent = node.GetParent()
        if parent is None:
            continue
        children = [node.GetChild(idx) for idx in range(node.GetChildCount())]
        for child in children:
            node.RemoveChild(child)
            parent.AddChild(child)
        parent.RemoveChild(node)
        nodes_by_uid.pop(uid, None)


def _apply_attribute_types(scene, nodes_by_uid: Dict[int, Any], attributes: Dict[int, Tuple[str, str]]) -> None:  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()
    skeleton_map = {
        "Root": fbx.FbxSkeleton.eRoot,
        "Limb": fbx.FbxSkeleton.eLimb,
        "LimbNode": fbx.FbxSkeleton.eLimbNode,
        "Effector": fbx.FbxSkeleton.eEffector,
    }

    for uid, (attr_type, _attr_class) in attributes.items():
        node = nodes_by_uid.get(uid)
        if node is None:
            continue
        node_attribute = node.GetNodeAttribute()

        if attr_type in skeleton_map:
            if not isinstance(node_attribute, fbx.FbxSkeleton):
                skeleton = fbx.FbxSkeleton.Create(scene, node.GetName() or "Skeleton")
                node.SetNodeAttribute(skeleton)
                node_attribute = skeleton
            node_attribute.SetSkeletonType(skeleton_map[attr_type])
        elif attr_type == "Node" and isinstance(node_attribute, fbx.FbxSkeleton):
            node.SetNodeAttribute(None)

