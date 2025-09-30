"""Expose tree summaries for top-level scene nodes."""

from __future__ import annotations

from typing import Any, Dict, List

from ..core import sdk
from ..core.analyzer import SceneContext, SceneInspector


class TopLevelInspector(SceneInspector):
    id = "top_level_nodes"

    def collect(self, context: SceneContext) -> List[Dict[str, Any]]:
        fbx, _ = sdk.import_fbx_module()
        root = context.root_node
        summary: List[Dict[str, Any]] = []

        mesh_enum = getattr(fbx.FbxNodeAttribute, "eMesh", None)

        for idx in range(root.GetChildCount()):
            node = root.GetChild(idx)
            attribute = node.GetNodeAttribute()
            attr_type = attribute.GetTypeName() if attribute else "None"
            attr_class = attribute.__class__.__name__ if attribute else "(NoAttribute)"
            attr_enum = attribute.GetAttributeType() if hasattr(attribute, "GetAttributeType") else None

            summary.append(
                {
                    "name": node.GetName() or f"Node_{node.GetUniqueID()}",
                    "attribute_type": attr_type,
                    "attribute_class": attr_class,
                    "child_count": node.GetChildCount(),
                    "is_mesh": bool(mesh_enum is not None and attr_enum == mesh_enum),
                }
            )

        return summary
