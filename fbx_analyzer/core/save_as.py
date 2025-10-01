"""Helpers for exporting modified FBX scenes without touching the original."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..models import SceneNode
from . import sdk
from .exceptions import FBXLoadError, FBXSaveError


def save_scene_graph_as(source_path: str, target_path: str, scene_graph: Optional[SceneNode]) -> None:
    """Persist edits in ``scene_graph`` into a copy of ``source_path``."""

    target_path = str(target_path)
    if os.path.abspath(source_path) == os.path.abspath(target_path):
        raise FBXSaveError("The destination path must be different from the source path.")

    Path(target_path).parent.mkdir(parents=True, exist_ok=True)

    if scene_graph is None:
        try:
            shutil.copy2(source_path, target_path)
        except OSError as exc:  # pragma: no cover
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
    scene_root = scene.GetRootNode()
    existing_nodes = _map_scene_nodes(scene_root)
    used_uids: set[int] = set()

    def sync(node_model: SceneNode, parent_fbx) -> Any:  # type: ignore[valid-type]
        fbx, _ = sdk.import_fbx_module()

        fbx_node = None
        if node_model.uid is not None:
            fbx_node = existing_nodes.get(node_model.uid)

        if fbx_node is None:
            name = node_model.name or "Node"
            fbx_node = fbx.FbxNode.Create(scene, name)
            parent_fbx.AddChild(fbx_node)
            node_model.uid = fbx_node.GetUniqueID()
            existing_nodes[node_model.uid] = fbx_node
        else:
            current_parent = fbx_node.GetParent()
            if current_parent is not parent_fbx:
                if current_parent is not None:
                    current_parent.RemoveChild(fbx_node)
                parent_fbx.AddChild(fbx_node)

        used_uids.add(fbx_node.GetUniqueID())

        _apply_node_attribute(scene, fbx_node, node_model.attribute_type, node_model.attribute_class)
        _apply_node_transform(fbx_node, node_model)

        desired_children = []
        for child_model in node_model.children:
            child_fbx = sync(child_model, fbx_node)
            desired_children.append(child_fbx)

        desired_ids = {child.GetUniqueID() for child in desired_children}
        for idx in reversed(range(fbx_node.GetChildCount())):
            child = fbx_node.GetChild(idx)
            if child.GetUniqueID() not in desired_ids:
                fbx_node.RemoveChild(child)

        return fbx_node

    sync(scene_graph, scene_root)

    # Remove nodes that are not part of the desired scene graph
    for uid, node in list(existing_nodes.items()):
        if uid == scene_root.GetUniqueID():
            continue
        if uid not in used_uids:
            parent = node.GetParent()
            if parent is None:
                continue
            children = [node.GetChild(i) for i in range(node.GetChildCount())]
            for child in children:
                node.RemoveChild(child)
                parent.AddChild(child)
            parent.RemoveChild(node)


def _map_scene_nodes(root) -> Dict[int, Any]:  # type: ignore[valid-type]
    mapping: Dict[int, Any] = {}

    def walk(node) -> None:  # type: ignore[valid-type]
        mapping[node.GetUniqueID()] = node
        for idx in range(node.GetChildCount()):
            walk(node.GetChild(idx))

    walk(root)
    return mapping


def _resolve_enum_value(enum_holder, target_name: str):
    if hasattr(enum_holder, target_name):
        return getattr(enum_holder, target_name)
    for attr in dir(enum_holder):
        if attr.lower() == target_name.lower():
            return getattr(enum_holder, attr)
    nested = getattr(enum_holder, "EType", None)
    if nested is not None:
        for attr in dir(nested):
            if attr.lower() == target_name.lower():
                return getattr(nested, attr)
    return None


def _apply_node_attribute(scene, node, attr_type: str, attr_class: str) -> None:  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()
    skeleton_labels = {"Root": "eRoot", "Limb": "eLimb", "LimbNode": "eLimbNode", "Effector": "eEffector"}
    skeleton_types = {label: _resolve_enum_value(fbx.FbxSkeleton, value) for label, value in skeleton_labels.items()}

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
