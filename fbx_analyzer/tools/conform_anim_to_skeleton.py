from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..core import sdk
from ..inspectors.skeleton import SkeletonInspector


RestTRS = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def _build_skeleton_map(
    skeleton_path: str,
) -> Tuple[str, Optional[str], Optional[RestTRS], Dict[str, Optional[str]], Dict[str, str], Dict[str, RestTRS]]:
    """Gather skeleton hierarchy information required for conforming."""

    fbx_module, _ = sdk.import_fbx_module()
    inspector = SkeletonInspector()
    from ..core.analyzer import FBXAnalyzer

    with FBXAnalyzer(skeleton_path) as analyzer:
        result = analyzer.run([inspector])
    skeletons = result.get(inspector.id, []) or []
    if not skeletons:
        raise RuntimeError("No skeleton joints detected in skeleton file")

    skeleton = skeletons[0]

    parent_map: Dict[str, Optional[str]] = {}
    joint_type_map: Dict[str, str] = {}
    rest_trs_map: Dict[str, RestTRS] = {}

    def walk(node, parent_name: Optional[str]) -> None:
        parent_map[node.name] = parent_name
        joint_type_map[node.name] = node.joint_type
        rest_trs_map[node.name] = (
            tuple(node.transform.translation),
            tuple(node.transform.rotation),
            tuple(node.transform.scaling),
        )
        for child in node.children:
            walk(child, node.name)

    walk(skeleton.root, None)

    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        scene = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, scene, skeleton_path):
            raise RuntimeError("Failed to reload skeleton for container detection")

        def find_skeleton_root() -> Any:
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

            skeleton_root = locate(scene.GetRootNode())
            if skeleton_root is None:
                raise RuntimeError("Unable to locate skeleton root joint")
            return skeleton_root

        skeleton_root_node = find_skeleton_root()
        parent = skeleton_root_node.GetParent()
        container_name = None
        container_trs: Optional[RestTRS] = None
        scene_root = scene.GetRootNode()
        if parent is not None and parent != scene_root:
            container_name = parent.GetName() or None
            container_trs = (
                tuple(parent.LclTranslation.Get()),
                tuple(parent.LclRotation.Get()),
                tuple(parent.LclScaling.Get()),
            )
    finally:
        sdk.destroy_manager(mgr)

    return skeleton.root.name, container_name, container_trs, parent_map, joint_type_map, rest_trs_map


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


def _node_has_any_curve(node, layers: List[Any]) -> bool:  # type: ignore[valid-type]
    props = (node.LclTranslation, node.LclRotation, node.LclScaling)
    for layer in layers:
        for prop in props:
            try:
                curve_node = prop.GetCurveNode(layer)
            except Exception:
                curve_node = None
            if curve_node:
                return True
    return False


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


def _choose_primary_node(
    candidates: List[Any],
    layers: List[Any],
    fbx_module,
    *,
    prefer_skeleton: bool,
) -> Any:  # type: ignore[valid-type]
    if prefer_skeleton:
        for node in candidates:
            attr = node.GetNodeAttribute()
            try:
                if attr and isinstance(attr, fbx_module.FbxSkeleton):
                    return node
            except Exception:
                pass
    for node in candidates:
        if _node_has_any_curve(node, layers):
            return node
    return candidates[0]


def _deduplicate_nodes_by_name(
    scene,
    names: Set[str],
    layers: List[Any],
    *,
    prefer_skeleton: bool,
) -> None:  # type: ignore[valid-type]
    if not names:
        return

    fbx_module, _ = sdk.import_fbx_module()
    root = scene.GetRootNode()
    if root is None:
        return

    for name in names:
        matches = _find_nodes_by_name(root, name)
        if len(matches) <= 1:
            continue
        primary = _choose_primary_node(matches, layers, fbx_module, prefer_skeleton=prefer_skeleton)
        for node in matches:
            if node is primary:
                continue
            children = [node.GetChild(i) for i in range(node.GetChildCount())]
            for child in children:
                try:
                    node.RemoveChild(child)
                    primary.AddChild(child)
                except Exception:
                    pass
            parent = node.GetParent()
            if parent:
                try:
                    parent.RemoveChild(node)
                except Exception:
                    pass


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


def _find_or_create_node(scene, name: str, *, as_skeleton: bool) -> Any:  # type: ignore[valid-type]
    fbx_module, _ = sdk.import_fbx_module()
    root = scene.GetRootNode()
    matches = _find_nodes_by_name(root, name)

    node = None
    if matches:
        if as_skeleton:
            for candidate in matches:
                attr = candidate.GetNodeAttribute()
                try:
                    if attr and isinstance(attr, fbx_module.FbxSkeleton):
                        node = candidate
                        break
                except Exception:
                    pass
        else:
            for candidate in matches:
                attr = candidate.GetNodeAttribute()
                try:
                    if attr and isinstance(attr, fbx_module.FbxNull):
                        node = candidate
                        break
                except Exception:
                    pass
        if node is None:
            node = matches[0]

    if node is None:
        node = fbx_module.FbxNode.Create(scene, name)
        root.AddChild(node)

    if as_skeleton:
        attr = node.GetNodeAttribute()
        if not (attr and isinstance(attr, fbx_module.FbxSkeleton)):
            skeleton_attr = fbx_module.FbxSkeleton.Create(scene, name or "Skeleton")
            try:
                skeleton_attr.SetSkeletonType(fbx_module.FbxSkeleton.EType.eLimbNode)
            except Exception:
                pass
            node.SetNodeAttribute(skeleton_attr)
    else:
        attr = node.GetNodeAttribute()
        if not (attr and isinstance(attr, fbx_module.FbxNull)):
            node.SetNodeAttribute(fbx_module.FbxNull.Create(scene, name or "Null"))

    return node


def conform_animation_to_skeleton(skeleton_path: str, animation_path: str, output_path: str) -> None:
    """Make the animation FBX's bone hierarchy and root container match the skeleton."""

    fbx_module, _ = sdk.import_fbx_module()
    (
        root_joint,
        container_name,
        container_trs,
        parent_map,
        joint_type_map,
        rest_trs_map,
    ) = _build_skeleton_map(skeleton_path)

    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        scene = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, scene, animation_path):
            raise RuntimeError(f"Failed to load animation FBX: {animation_path}")

        # Align global settings with the skeleton scene
        try:
            mgr2 = sdk.create_manager()
            sdk.create_io_settings(mgr2)
            scene2 = sdk.create_scene(mgr2)
            if sdk.load_scene(mgr2, scene2, skeleton_path):
                globals_anim = scene.GetGlobalSettings()
                globals_skel = scene2.GetGlobalSettings()
                globals_anim.SetAxisSystem(globals_skel.GetAxisSystem())
                globals_anim.SetSystemUnit(globals_skel.GetSystemUnit())
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

        skeleton_names: Set[str] = set(parent_map.keys())
        _deduplicate_nodes_by_name(scene, skeleton_names, layers, prefer_skeleton=True)
        if container_name:
            _deduplicate_nodes_by_name(scene, {container_name}, layers, prefer_skeleton=False)

        root_node = _find_or_create_node(scene, root_joint, as_skeleton=True)

        removal_chain: List[Any] = []
        top_ancestor: Optional[Any] = None
        walker = root_node
        while True:
            parent = walker.GetParent()
            if parent is None:
                break
            if parent is scene_root:
                if walker is not root_node:
                    top_ancestor = walker
                break
            removal_chain.append(parent)
            walker = parent

        container_node = None
        if container_name:
            container_matches = _find_nodes_by_name(scene_root, container_name)
            if container_matches:
                container_node = _choose_primary_node(container_matches, layers, fbx_module, prefer_skeleton=False)
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
            if container_node is None:
                container_node = fbx_module.FbxNode.Create(scene, container_name)
                scene_root.AddChild(container_node)

            attr = container_node.GetNodeAttribute()
            if not (attr and isinstance(attr, fbx_module.FbxNull)):
                container_node.SetNodeAttribute(fbx_module.FbxNull.Create(scene, container_name))

            if container_node.GetParent() is not scene_root:
                try:
                    (container_node.GetParent() or scene_root).RemoveChild(container_node)
                except Exception:
                    pass
                scene_root.AddChild(container_node)

            if top_ancestor is not None:
                container_node.LclTranslation.Set(top_ancestor.LclTranslation.Get())
                container_node.LclRotation.Set(top_ancestor.LclRotation.Get())
                scale_vals = tuple(top_ancestor.LclScaling.Get())
            elif container_trs is not None:
                container_node.LclTranslation.Set(fbx_module.FbxDouble3(*container_trs[0]))
                container_node.LclRotation.Set(fbx_module.FbxDouble3(*container_trs[1]))
                scale_vals = container_trs[2]
            else:
                container_node.LclTranslation.Set(fbx_module.FbxDouble3(0.0, 0.0, 0.0))
                container_node.LclRotation.Set(fbx_module.FbxDouble3(0.0, 0.0, 0.0))
                scale_vals = tuple(container_node.LclScaling.Get())
            scale_vals = tuple(1.0 if abs(v) < 1e-6 else v for v in scale_vals)
            container_node.LclScaling.Set(fbx_module.FbxDouble3(*scale_vals))
        else:
            container_node = None

        target_parent = container_node or scene_root
        if root_node.GetParent() is not target_parent:
            try:
                (root_node.GetParent() or scene_root).RemoveChild(root_node)
            except Exception:
                pass
            target_parent.AddChild(root_node)

        removal_nodes: List[Any] = []
        if top_ancestor is not None and top_ancestor is not container_node:
            removal_nodes.append(top_ancestor)
        removal_nodes.extend(removal_chain)

        seen_nodes: Set[int] = set()
        filtered_nodes: List[Any] = []
        for node in removal_nodes:
            if node is None:
                continue
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            filtered_nodes.append(node)

        for ancestor in filtered_nodes:
            if ancestor in (container_node, root_node, scene_root):
                continue
            children = [ancestor.GetChild(i) for i in range(ancestor.GetChildCount())]
            for child in children:
                if child is container_node or child is root_node:
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
            for candidate in _find_nodes_by_name(scene_root, root_joint):
                if candidate is root_node:
                    continue
                if candidate.GetParent() is scene_root:
                    try:
                        scene_root.RemoveChild(candidate)
                    except Exception:
                        pass

        if container_node is not None:
            root_matches = _find_nodes_by_name(scene_root, root_joint)
            primary_root = None
            for candidate in root_matches:
                if candidate.GetParent() is container_node:
                    primary_root = candidate
                    break
            if primary_root is None and root_matches:
                primary_root = root_matches[0]
                if primary_root.GetParent() is not container_node:
                    try:
                        (primary_root.GetParent() or scene_root).RemoveChild(primary_root)
                    except Exception:
                        pass
                    container_node.AddChild(primary_root)
            if primary_root is not None:
                root_node = primary_root
            for candidate in root_matches:
                if candidate is root_node:
                    continue
                if candidate.GetParent() is scene_root:
                    try:
                        scene_root.RemoveChild(candidate)
                    except Exception:
                        pass
            _ensure_trs_keys(container_node, anim_layer)

        type_map = {
            "Root": fbx_module.FbxSkeleton.EType.eRoot,
            "LimbNode": fbx_module.FbxSkeleton.EType.eLimbNode,
            "Effector": fbx_module.FbxSkeleton.EType.eEffector,
        }

        children_lookup: Dict[str, List[str]] = {}
        for child_name, parent_name in parent_map.items():
            if parent_name is None:
                continue
            children_lookup.setdefault(parent_name, []).append(child_name)

        for name, parent_name in parent_map.items():
            node = _find_or_create_node(scene, name, as_skeleton=True)
            try:
                attr = node.GetNodeAttribute()
                if isinstance(attr, fbx_module.FbxSkeleton):
                    if name == root_joint:
                        attr.SetSkeletonType(type_map["Root"])
                    else:
                        has_children = name in children_lookup
                        if not has_children or name.endswith("_end"):
                            attr.SetSkeletonType(type_map["Effector"])
                        else:
                            attr.SetSkeletonType(type_map["LimbNode"])
            except Exception:
                pass

            parent_node = root_node if parent_name is None else _find_or_create_node(scene, parent_name, as_skeleton=True)
            if node.GetParent() is not parent_node:
                try:
                    (node.GetParent() or scene_root).RemoveChild(node)
                except Exception:
                    pass
                parent_node.AddChild(node)

            trs = rest_trs_map.get(name)
            if trs is not None:
                t, r, s = trs
            else:
                t = (0.0, 0.0, 0.0)
                r = (0.0, 0.0, 0.0)
                s = (1.0, 1.0, 1.0)
            try:
                node.LclTranslation.Set(fbx_module.FbxDouble3(*t))
                node.LclRotation.Set(fbx_module.FbxDouble3(*r))
                node.LclScaling.Set(fbx_module.FbxDouble3(*s))
            except Exception:
                pass

            _ensure_trs_keys(node, anim_layer)

        _deduplicate_nodes_by_name(scene, skeleton_names, layers, prefer_skeleton=True)
        if container_node is not None and container_name:
            _deduplicate_nodes_by_name(scene, {container_name}, layers, prefer_skeleton=False)

        for name in parent_map.keys():
            matches = _find_nodes_by_name(scene_root, name)
            if not matches:
                continue
            node = matches[0]
            attr = node.GetNodeAttribute()
            if not (attr and isinstance(attr, fbx_module.FbxSkeleton)):
                continue
            if name == root_joint:
                attr.SetSkeletonType(type_map["Root"])
            else:
                has_children = name in children_lookup
                if not has_children or name.endswith("_end"):
                    attr.SetSkeletonType(type_map["Effector"])
                else:
                    attr.SetSkeletonType(type_map["LimbNode"])

        if not sdk.save_scene(mgr, scene, output_path):
            raise RuntimeError(f"Failed to save conformed animation to: {output_path}")
    finally:
        sdk.destroy_manager(mgr)
