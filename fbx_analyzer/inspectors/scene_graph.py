"""Scene graph inspector."""

from __future__ import annotations

from typing import Dict, Tuple

from ..core import sdk
from ..core.analyzer import SceneContext, SceneInspector
from ..models import SceneNode
from ..utils import double3_to_tuple


class SceneGraphInspector(SceneInspector):
    """Capture the entire scene hierarchy as editable ``SceneNode`` models."""

    id = "scene_graph"

    def collect(self, context: SceneContext) -> SceneNode:
        fbx, _ = sdk.import_fbx_module()

        def build(node, path: Tuple[int, ...]) -> SceneNode:  # type: ignore[valid-type]
            attribute = node.GetNodeAttribute()
            attribute_type = attribute.GetTypeName() if attribute else "None"
            attribute_class = attribute.__class__.__name__ if attribute else "(NoAttribute)"

            scene_node = SceneNode(
                name=node.GetName() or f"Node_{node.GetUniqueID()}",
                attribute_type=attribute_type,
                attribute_class=attribute_class,
                translation=double3_to_tuple(node.LclTranslation.Get()),
                rotation=double3_to_tuple(node.LclRotation.Get()),
                scaling=double3_to_tuple(node.LclScaling.Get()),
                child_count=node.GetChildCount(),
                uid=node.GetUniqueID(),
                parent_uid=node.GetParent().GetUniqueID() if node.GetParent() else None,
                original_path=path,
                properties=_collect_user_properties(node, fbx),
            )

            for idx in range(node.GetChildCount()):
                child_path = path + (idx,)
                scene_node.children.append(build(node.GetChild(idx), child_path))

            scene_node.child_count = len(scene_node.children)
            return scene_node

        return build(context.root_node, ())


def _collect_user_properties(node, fbx_module) -> Dict[str, str]:  # type: ignore[valid-type]
    """Gather user-defined properties from an FBX node."""

    properties: Dict[str, str] = {}
    prop = node.GetFirstProperty()
    while prop.IsValid():
        try:
            if prop.GetFlag(fbx_module.FbxPropertyFlags.eUserDefined):
                properties[prop.GetName()] = str(prop.Get())
        except Exception:
            pass
        prop = node.GetNextProperty(prop)
    return properties
