from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..core import sdk


def _collect_root_info(
    skel_scene,
) -> Tuple[str, Optional[str], Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]]]:
    """Return (root_joint_name, container_name, container_trs) from the skeleton scene."""

    root = skel_scene.GetRootNode()
    if root is None:
        raise RuntimeError("Skeleton scene has no root node")

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

    def locate_root(node):
        for idx in range(node.GetChildCount()):
            child = node.GetChild(idx)
            if is_skeleton(child):
                parent = child.GetParent()
                if not (parent and is_skeleton(parent)):
                    return child
            result = locate_root(child)
            if result is not None:
                return result
        return None

    root_joint = locate_root(root)
    if root_joint is None:
        raise RuntimeError("Unable to locate skeleton root joint in skeleton scene")

    parent = root_joint.GetParent()
    container_name = None
    container_trs = None
    if parent is not None and parent != root:
        container_name = parent.GetName() or None
        container_trs = (
            tuple(parent.LclTranslation.Get()),
            tuple(parent.LclRotation.Get()),
            tuple(parent.LclScaling.Get()),
        )

    return root_joint.GetName() or "", container_name, container_trs


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
            lyr = scene.GetSrcObject(crit, index)
            if lyr:
                layers.append(lyr)
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
                lyr = stack.GetMember(member_index)
                if lyr:
                    layers.append(lyr)
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


def _ensure_identity_curves(node, layer):  # type: ignore[valid-type]
    fbx_module, _ = sdk.import_fbx_module()

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
            t = fbx_module.FbxTime()
            t.SetSecondDouble(0.0)
            index = curve.KeyAdd(t)
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

    for comp, val in (('X', 0.0), ('Y', 0.0), ('Z', 0.0)):
        ensure(node.LclTranslation, comp, val)
        ensure(node.LclRotation, comp, val)
    for comp, val in (('X', 1.0), ('Y', 1.0), ('Z', 1.0)):
        ensure(node.LclScaling, comp, val)


def add_missing_root_track(skeleton_path: str, animation_path: str, output_path: str) -> None:
    """Add an explicit root track to the animation so it matches the skeleton."""

    fbx_module, _ = sdk.import_fbx_module()
    # Gather skeleton metadata
    mgr_skel = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr_skel)
        scene_skel = sdk.create_scene(mgr_skel)
        if not sdk.load_scene(mgr_skel, scene_skel, skeleton_path):
            raise RuntimeError(f"Failed to load skeleton FBX: {skeleton_path}")
        root_name, container_name, container_trs = _collect_root_info(scene_skel)
    finally:
        sdk.destroy_manager(mgr_skel)

    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        scene = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, scene, animation_path):
            raise RuntimeError(f"Failed to load animation FBX: {animation_path}")

        # Align global settings with skeleton
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

        # Identify the animated root joint in the scene
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

        # Capture ancestor chain up to the scene root
        ancestors: List[Any] = []
        walker = animated_root
        while walker is not None and walker is not scene_root:
            parent = walker.GetParent()
            if parent is None:
                break
            ancestors.append(parent)
            if parent is scene_root:
                break
            walker = parent
        top_ancestor = ancestors[-1] if ancestors and ancestors[-1].GetParent() is scene_root else None

        # Prepare or create the container node
        container_node = None
        if container_name:
            container_matches = _find_nodes_by_name(scene_root, container_name)
            container_node = None
            for candidate in container_matches:
                attr = candidate.GetNodeAttribute()
                try:
                    if attr and isinstance(attr, fbx_module.FbxNull):
                        container_node = candidate
                        break
                except Exception:
                    pass
            if container_node is None and container_matches:
                container_node = container_matches[0]
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
            elif top_ancestor is not None and top_ancestor is not container_node:
                container_node.LclTranslation.Set(top_ancestor.LclTranslation.Get())
                container_node.LclRotation.Set(top_ancestor.LclRotation.Get())
                scale_vals = tuple(top_ancestor.LclScaling.Get())
            else:
                scale_vals = tuple(container_node.LclScaling.Get())
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

        for ancestor in reversed(ancestors):
            if ancestor is container_node:
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
            _ensure_identity_curves(container_node, anim_layer)
        _ensure_identity_curves(animated_root, anim_layer)

        if not sdk.save_scene(mgr, scene, output_path):
            raise RuntimeError(f"Failed to save patched animation to: {output_path}")
    finally:
        sdk.destroy_manager(mgr)
