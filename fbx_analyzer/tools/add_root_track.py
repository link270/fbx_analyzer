from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..core import sdk


RestTRS = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def _collect_root_info(skeleton_path: str) -> Tuple[str, Optional[str], Optional[RestTRS]]:
    """Return the skeleton root joint name, container name, and container TRS."""

    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        scene = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, scene, skeleton_path):
            raise RuntimeError(f"Failed to load skeleton FBX: {skeleton_path}")

        fbx_module, _ = sdk.import_fbx_module()
        skeleton_class = getattr(fbx_module, "FbxSkeleton", None)
        if skeleton_class is None:
            raise RuntimeError("FbxSkeleton class unavailable in SDK")

        def is_skeleton(node) -> bool:
            attr = node.GetNodeAttribute()
            try:
                return bool(attr and isinstance(attr, skeleton_class))
            except Exception:
                return False

        def locate(node):
            for idx in range(node.GetChildCount()):
                child = node.GetChild(idx)
                if is_skeleton(child):
                    parent = child.GetParent()
                    if not (parent and is_skeleton(parent)):
                        return child
                result = locate(child)
                if result is not None:
                    return result
            return None

        root_node = scene.GetRootNode()
        skeleton_root = locate(root_node)
        if skeleton_root is None:
            raise RuntimeError("Unable to locate skeleton root joint in skeleton scene")

        parent = skeleton_root.GetParent()
        container_name = None
        container_trs = None
        if parent is not None and parent != root_node:
            container_name = parent.GetName() or None
            container_trs = (
                tuple(parent.LclTranslation.Get()),
                tuple(parent.LclRotation.Get()),
                tuple(parent.LclScaling.Get()),
            )

        return skeleton_root.GetName() or "", container_name, container_trs
    finally:
        sdk.destroy_manager(mgr)


def _find_nodes_by_name(root, name: str) -> List[Any]:  # type: ignore[valid-type]
    matches: List[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if (node.GetName() or "") == name:
            matches.append(node)
        for idx in range(node.GetChildCount()):
            stack.append(node.GetChild(idx))
    return matches


def _collect_anim_layers(scene, fbx_module) -> List[Any]:  # type: ignore[name-defined]
    layers: List[Any] = []
    try:
        crit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimLayer.ClassId)
        count = scene.GetSrcObjectCount(crit)
        for index in range(count or 0):
            layer = scene.GetSrcObject(crit, index)
            if layer:
                layers.append(layer)
    except Exception:
        pass
    if layers:
        return layers

    try:
        stack_crit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId)
        stack_count = scene.GetSrcObjectCount(stack_crit)
        for s_index in range(stack_count or 0):
            stack = scene.GetSrcObject(stack_crit, s_index)
            if not stack:
                continue
            try:
                member_count = stack.GetMemberCount()
            except Exception:
                member_count = 0
            for member_index in range(member_count or 0):
                layer = stack.GetMember(member_index)
                if layer:
                    layers.append(layer)
    except Exception:
        pass
    return layers


def _ensure_layer(scene):  # type: ignore[valid-type]
    fbx_module, _ = sdk.import_fbx_module()
    stack = None
    try:
        crit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId)
        if scene.GetSrcObjectCount(crit) > 0:
            stack = scene.GetSrcObject(crit, 0)
    except Exception:
        stack = None
    if stack is None:
        stack = fbx_module.FbxAnimStack.Create(scene, "AnimStack")
    layer = None
    try:
        if hasattr(stack, "GetMemberCount") and stack.GetMemberCount() > 0:
            layer = stack.GetMember(0)
    except Exception:
        layer = None
    if layer is None:
        layer = fbx_module.FbxAnimLayer.Create(scene, "BaseLayer")
        try:
            stack.AddMember(layer)
        except Exception:
            pass
    return layer


def _ensure_trs_keys(node, layer, *, include_scale: bool = True):  # type: ignore[valid-type]
    fbx_module, _ = sdk.import_fbx_module()

    translation = tuple(node.LclTranslation.Get())
    rotation = tuple(node.LclRotation.Get())
    scale = tuple(node.LclScaling.Get())

    def ensure(prop, component, value):
        try:
            curve = prop.GetCurve(layer, component, True)
        except Exception:
            curve = None
        if curve is None:
            return
        if hasattr(curve, "KeyGetCount") and curve.KeyGetCount() > 0:
            return
        try:
            curve.KeyModifyBegin()
            time = fbx_module.FbxTime()
            time.SetSecondDouble(0.0)
            index = curve.KeyAdd(time)
            try:
                curve.KeySetValue(index, value)
            except Exception:
                pass
            try:
                curve.KeySetInterpolation(index, fbx_module.FbxAnimCurveDef.eInterpolationConstant)
            except Exception:
                pass
            curve.KeyModifyEnd()
        except Exception:
            pass

    for comp, val in zip(('X', 'Y', 'Z'), translation):
        ensure(node.LclTranslation, comp, val)
    for comp, val in zip(('X', 'Y', 'Z'), rotation):
        ensure(node.LclRotation, comp, val)
    if include_scale:
        for comp, val in zip(('X', 'Y', 'Z'), scale):
            ensure(node.LclScaling, comp, val)


def add_missing_root_track(skeleton_path: str, animation_path: str, output_path: str) -> None:
    """Add a matching root track to the animation so it imports cleanly in UE."""

    fbx_module, _ = sdk.import_fbx_module()
    root_name, container_name, container_trs = _collect_root_info(skeleton_path)

    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        scene = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, scene, animation_path):
            raise RuntimeError(f"Failed to load animation FBX: {animation_path}")

        # Align global settings with the skeleton
        try:
            mgr2 = sdk.create_manager()
            sdk.create_io_settings(mgr2)
            scene2 = sdk.create_scene(mgr2)
            if sdk.load_scene(mgr2, scene2, skeleton_path):
                gs_anim = scene.GetGlobalSettings()
                gs_skel = scene2.GetGlobalSettings()
                gs_anim.SetAxisSystem(gs_skel.GetAxisSystem())
                gs_anim.SetSystemUnit(gs_skel.GetSystemUnit())
        finally:
            try:
                sdk.destroy_manager(mgr2)
            except Exception:
                pass

        scene_root = scene.GetRootNode()
        if scene_root is None:
            raise RuntimeError("Animation scene has no root node")

        layers = _collect_anim_layers(scene, fbx_module)
        anim_layer = _ensure_layer(scene)
        if anim_layer not in layers:
            layers.append(anim_layer)

        root_matches = _find_nodes_by_name(scene_root, root_name)
        if not root_matches:
            raise RuntimeError(f"Root joint '{root_name}' not found in animation scene")

        animated_root = root_matches[0]
        for duplicate in root_matches[1:]:
            if duplicate is animated_root:
                continue
            children = [duplicate.GetChild(i) for i in range(duplicate.GetChildCount())]
            for child in children:
                try:
                    duplicate.RemoveChild(child)
                    animated_root.AddChild(child)
                except Exception:
                    pass
            parent = duplicate.GetParent()
            if parent:
                try:
                    parent.RemoveChild(duplicate)
                except Exception:
                    pass

        nearest_parent: Optional[Any] = None
        removal_chain: List[Any] = []
        top_ancestor: Optional[Any] = None

        walker = animated_root
        while True:
            parent = walker.GetParent()
            if parent is None:
                break
            if parent is scene_root:
                if walker is not animated_root:
                    top_ancestor = walker
                break
            if nearest_parent is None:
                nearest_parent = parent
            removal_chain.append(parent)
            walker = parent

        container_node = None
        if container_name:
            container_matches = _find_nodes_by_name(scene_root, container_name)
            preferred = None
            for node in container_matches:
                attr = node.GetNodeAttribute()
                try:
                    if attr and isinstance(attr, fbx_module.FbxNull):
                        preferred = node
                        break
                except Exception:
                    pass
            container_node = preferred or (container_matches[0] if container_matches else None)
            if container_node is None:
                container_node = fbx_module.FbxNode.Create(scene, container_name)
                scene_root.AddChild(container_node)
            else:
                for other in container_matches:
                    if other is container_node:
                        continue
                    children = [other.GetChild(i) for i in range(other.GetChildCount())]
                    for child in children:
                        try:
                            other.RemoveChild(child)
                            container_node.AddChild(child)
                        except Exception:
                            pass
                    parent = other.GetParent()
                    if parent:
                        try:
                            parent.RemoveChild(other)
                        except Exception:
                            pass

            attr = container_node.GetNodeAttribute()
            if not (attr and isinstance(attr, fbx_module.FbxNull)):
                container_node.SetNodeAttribute(fbx_module.FbxNull.Create(scene, container_name))

            if container_node.GetParent() is not scene_root:
                try:
                    (container_node.GetParent() or scene_root).RemoveChild(container_node)
                except Exception:
                    pass
                scene_root.AddChild(container_node)

            if container_trs is not None:
                container_node.LclTranslation.Set(fbx_module.FbxDouble3(*container_trs[0]))
                container_node.LclRotation.Set(fbx_module.FbxDouble3(*container_trs[1]))
                scale_vals = container_trs[2]
            elif nearest_parent is not None:
                container_node.LclTranslation.Set(nearest_parent.LclTranslation.Get())
                container_node.LclRotation.Set(nearest_parent.LclRotation.Get())
                scale_vals = tuple(nearest_parent.LclScaling.Get())
            elif top_ancestor is not None:
                container_node.LclTranslation.Set(top_ancestor.LclTranslation.Get())
                container_node.LclRotation.Set(top_ancestor.LclRotation.Get())
                scale_vals = tuple(top_ancestor.LclScaling.Get())
            else:
                container_node.LclTranslation.Set(fbx_module.FbxDouble3(0.0, 0.0, 0.0))
                container_node.LclRotation.Set(fbx_module.FbxDouble3(0.0, 0.0, 0.0))
                scale_vals = (1.0, 1.0, 1.0)

            scale_vals = tuple(1.0 if abs(v) < 1e-6 else v for v in scale_vals)
            container_node.LclScaling.Set(fbx_module.FbxDouble3(*scale_vals))
        else:
            container_node = None

        target_parent = container_node or scene_root
        if animated_root.GetParent() is not target_parent:
            try:
                (animated_root.GetParent() or scene_root).RemoveChild(animated_root)
            except Exception:
                pass
            target_parent.AddChild(animated_root)

        removal_nodes: List[Any] = []
        if top_ancestor is not None and top_ancestor is not container_node:
            removal_nodes.append(top_ancestor)
        removal_nodes.extend(removal_chain)

        filtered: List[Any] = []
        seen: Set[int] = set()
        for node in removal_nodes:
            if node is None:
                continue
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            filtered.append(node)

        for ancestor in filtered:
            if ancestor in (container_node, animated_root, scene_root):
                continue
            children = [ancestor.GetChild(i) for i in range(ancestor.GetChildCount())]
            for child in children:
                if child is container_node or child is animated_root:
                    continue
                try:
                    ancestor.RemoveChild(child)
                    (container_node or scene_root).AddChild(child)
                except Exception:
                    pass
            parent = ancestor.GetParent()
            if parent:
                try:
                    parent.RemoveChild(ancestor)
                except Exception:
                    pass

        if container_node is not None:
            root_matches = _find_nodes_by_name(scene_root, root_name)
            for candidate in root_matches:
                if candidate is animated_root:
                    continue
                if candidate.GetParent() is scene_root:
                    try:
                        scene_root.RemoveChild(candidate)
                    except Exception:
                        pass
            _ensure_trs_keys(container_node, anim_layer)
        _ensure_trs_keys(animated_root, anim_layer)

        if not sdk.save_scene(mgr, scene, output_path):
            raise RuntimeError(f"Failed to save patched animation to: {output_path}")
    finally:
        sdk.destroy_manager(mgr)
