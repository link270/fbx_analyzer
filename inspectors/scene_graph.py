"""Scene graph inspector."""

from __future__ import annotations

from typing import Dict, Optional

from ..core import sdk
from ..core.analyzer import SceneContext, SceneInspector
from ..models import SceneNode


class SceneGraphInspector(SceneInspector):
    id = "scene_graph"

    def collect(self, context: SceneContext) -> SceneNode:
        fbx, _ = sdk.import_fbx_module()

        def build(node) -> SceneNode:
            attribute = node.GetNodeAttribute()
            attribute_type = attribute.GetTypeName() if attribute else "None"
            attribute_class = attribute.__class__.__name__ if attribute else "(NoAttribute)"

            def to_tuple(vector):
                return (float(vector[0]), float(vector[1]), float(vector[2]))

            properties: Dict[str, str] = {}
            prop = node.GetFirstProperty()
            while prop.IsValid():
                try:
                    if prop.GetFlag(fbx.FbxPropertyFlags.eUserDefined):
                        properties[prop.GetName()] = str(prop.Get())
                except Exception:
                    pass
                prop = node.GetNextProperty(prop)

            scene_node = SceneNode(
                name=node.GetName() or f"Node_{node.GetUniqueID()}",
                attribute_type=attribute_type,
                attribute_class=attribute_class,
                translation=to_tuple(node.LclTranslation.Get()),
                rotation=to_tuple(node.LclRotation.Get()),
                scaling=to_tuple(node.LclScaling.Get()),
                child_count=node.GetChildCount(),
                uid=node.GetUniqueID(),
                parent_uid=node.GetParent().GetUniqueID() if node.GetParent() else None,
                properties=properties,
            )

            for idx in range(node.GetChildCount()):
                scene_node.children.append(build(node.GetChild(idx)))

            scene_node.child_count = len(scene_node.children)
            return scene_node

        return build(context.root_node)
