from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple, Any, Set

from ..core import sdk
from ..inspectors.skeleton import SkeletonInspector


def _build_skeleton_map(skeleton_path: str) -> Tuple[str, Optional[str], Dict[str, Optional[str]], Dict[str, str], Dict[str, Tuple[Tuple[float,float,float],Tuple[float,float,float],Tuple[float,float,float]]]]:
    """Return (root_joint_name, expected_container_name, parent_map, joint_type_map, rest_trs_map).

    expected_container_name is the non-root parent of the skeleton root if present
    (e.g., a top Null UE treats as the skeleton root container).
    """

    fbx, _ = sdk.import_fbx_module()
    inspector = SkeletonInspector()
    # Use analyzer to get joints with parent_name relationships
    from ..core.analyzer import FBXAnalyzer

    with FBXAnalyzer(skeleton_path) as az:
        res = az.run([inspector])
    skeletons = res.get(inspector.id, []) or []
    if not skeletons:
        raise RuntimeError("No skeleton joints detected in skeleton file")

    skel = skeletons[0]
    # Build parent map from the first skeleton
    parent_map: Dict[str, Optional[str]] = {}
    joint_type_map: Dict[str, str] = {}
    rest_trs_map: Dict[str, Tuple[Tuple[float,float,float],Tuple[float,float,float],Tuple[float,float,float]]] = {}
    def walk(j, parent_name: Optional[str]):
        parent_map[j.name] = parent_name
        joint_type_map[j.name] = j.joint_type
        rest_trs_map[j.name] = (j.transform.translation, j.transform.rotation, j.transform.scaling)
        for c in j.children:
            walk(c, j.name)
    walk(skel.root, None)

    # Determine expected container (non-root parent of root joint if exists in FBX scene)
    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        sc = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, sc, skeleton_path):
            raise RuntimeError("Failed to reload skeleton for container detection")
        def find_node(root, name: str):
            if (root.GetName() or "") == name:
                return root
            for i in range(root.GetChildCount()):
                n = find_node(root.GetChild(i), name)
                if n: return n
            return None
        root_node = sc.GetRootNode()
        expected_container = None
        if root_node:
            node = find_node(root_node, skel.root.name)
            if node is not None:
                parent = node.GetParent()
                if parent and parent != root_node:
                    expected_container = parent.GetName() or None
    finally:
        sdk.destroy_manager(mgr)

    return skel.root.name, expected_container, parent_map, joint_type_map, rest_trs_map


def _find_or_create_node(scene, name: str, make_skeleton: bool = True):  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()
    root = scene.GetRootNode()
    def find(n):
        if (n.GetName() or "") == name:
            return n
        for i in range(n.GetChildCount()):
            r = find(n.GetChild(i))
            if r: return r
        return None
    node = find(root)
    if node is None:
        node = fbx.FbxNode.Create(scene, name)
        root.AddChild(node)
    if make_skeleton:
        attr = node.GetNodeAttribute()
        if not (attr and isinstance(attr, fbx.FbxSkeleton)):
            sk = fbx.FbxSkeleton.Create(scene, name or "Skeleton")
            try:
                sk.SetSkeletonType(getattr(fbx.FbxSkeleton, "eLimbNode", 1))
            except Exception:
                pass
            node.SetNodeAttribute(sk)
    else:
        # Ensure container uses FbxNull attribute for UE-style root containers
        try:
            attr = node.GetNodeAttribute()
            if not (attr and isinstance(attr, fbx.FbxNull)):
                null_attr = fbx.FbxNull.Create(scene, name or "Null")
                node.SetNodeAttribute(null_attr)
        except Exception:
            pass
    return node


def _ensure_layer(scene):  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()
    # Ensure stack + layer
    stack = None
    try:
        crit = fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId)
        cnt = scene.GetSrcObjectCount(crit)
        if cnt > 0:
            stack = scene.GetSrcObject(crit, 0)
    except Exception:
        stack = None
    if stack is None:
        stack = fbx.FbxAnimStack.Create(scene, "Stack")
    layer = None
    try:
        if hasattr(stack, "GetMemberCount") and stack.GetMemberCount() > 0:
            layer = stack.GetMember(0)
    except Exception:
        layer = None
    if layer is None:
        layer = fbx.FbxAnimLayer.Create(scene, "BaseLayer")
        try:
            stack.AddMember(layer)
        except Exception:
            pass
    return layer


def _collect_anim_layers(scene, fbx_module) -> list[Any]:  # type: ignore[name-defined]
    layers: list[Any] = []
    try:
        crit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimLayer.ClassId)
        cnt = scene.GetSrcObjectCount(crit)
        for i in range(cnt or 0):
            lyr = scene.GetSrcObject(crit, i)
            if lyr:
                layers.append(lyr)
    except Exception:
        pass
    if layers:
        return layers
    try:
        s_crit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId)
        scnt = scene.GetSrcObjectCount(s_crit)
        for si in range(scnt or 0):
            st = scene.GetSrcObject(s_crit, si)
            if not st:
                continue
            try:
                lc = st.GetMemberCount()
            except Exception:
                lc = 0
            for li in range(lc or 0):
                lyr = st.GetMember(li)
                if lyr:
                    layers.append(lyr)
    except Exception:
        pass
    return layers


def _node_has_any_curve(node, layers: list[Any]) -> bool:  # type: ignore[name-defined]
    props = (node.LclTranslation, node.LclRotation, node.LclScaling)
    for lyr in layers:
        for prop in props:
            try:
                cn = prop.GetCurveNode(lyr)
            except Exception:
                cn = None
            if cn:
                return True
    return False


def _deduplicate_nodes_by_name(scene, target_names: Set[str]) -> None:  # type: ignore[valid-type]
    """Ensure only one node exists per name in target_names; prefer the one with curves.

    Moves children to the primary node and removes duplicates to avoid UE renaming.
    """
    fbx, _ = sdk.import_fbx_module()
    layers = _collect_anim_layers(scene, fbx)

    name_map: Dict[str, list[Any]] = {}
    root = scene.GetRootNode()

    def walk(n):
        nm = n.GetName() or ""
        if nm in target_names:
            name_map.setdefault(nm, []).append(n)
        for i in range(n.GetChildCount()):
            walk(n.GetChild(i))

    walk(root)

    for nm, nodes in name_map.items():
        if len(nodes) <= 1:
            continue
        # Pick primary: one with animation curves; else first
        primary = None
        for cand in nodes:
            if _node_has_any_curve(cand, layers):
                primary = cand
                break
        if primary is None:
            primary = nodes[0]
        for dup in nodes:
            if dup is primary:
                continue
            # Move children under primary
            children = [dup.GetChild(i) for i in range(dup.GetChildCount())]
            for ch in children:
                try:
                    dup.RemoveChild(ch)
                    primary.AddChild(ch)
                except Exception:
                    pass
            # Try to remove duplicate
            parent = dup.GetParent()
            if parent:
                try:
                    parent.RemoveChild(dup)
                except Exception:
                    pass

def _ensure_identity_curves(node, layer):  # type: ignore[valid-type]
    fbx, _ = sdk.import_fbx_module()
    def ensure(prop, comp, val):
        try:
            curve = prop.GetCurve(layer, comp, True)
        except Exception:
            curve = None
        if curve is None:
            return
        try:
            curve.KeyModifyBegin()
            t = fbx.FbxTime()
            t.SetSecondDouble(0.0)
            idx = curve.KeyAdd(t)
            try:
                curve.KeySetValue(idx, val)
            except Exception:
                pass
            try:
                curve.KeySetInterpolation(idx, fbx.FbxAnimCurveDef.eInterpolationConstant)
            except Exception:
                pass
            curve.KeyModifyEnd()
        except Exception:
            pass
    for comp, val in (("X", 0.0), ("Y", 0.0), ("Z", 0.0)):
        ensure(node.LclTranslation, comp, val)
        ensure(node.LclRotation, comp, val)
    for comp, val in (("X", 1.0), ("Y", 1.0), ("Z", 1.0)):
        ensure(node.LclScaling, comp, val)


def conform_animation_to_skeleton(skeleton_path: str, animation_path: str, output_path: str) -> None:
    """Make the animation FBX's bone hierarchy and root container match the skeleton.

    - Ensures the expected container (if any) exists directly under scene root.
    - Reparents the skeleton root joint under the expected container.
    - Creates any missing bone nodes as FbxSkeleton and reparents to match parent map.
    - Adds identity TRS curves for bones missing curves.
    - Aligns GlobalSettings axis and units to the skeleton.
    """

    fbx, _ = sdk.import_fbx_module()
    root_joint, expected_container, parent_map, joint_type_map, rest_trs_map = _build_skeleton_map(skeleton_path)

    m = sdk.create_manager()
    try:
        sdk.create_io_settings(m)
        sc = sdk.create_scene(m)
        if not sdk.load_scene(m, sc, animation_path):
            raise RuntimeError(f"Failed to load animation FBX: {animation_path}")

        # Align GlobalSettings
        try:
            m2 = sdk.create_manager(); sdk.create_io_settings(m2); sc2 = sdk.create_scene(m2)
            if sdk.load_scene(m2, sc2, skeleton_path):
                sc.GetGlobalSettings().SetAxisSystem(sc2.GetGlobalSettings().GetAxisSystem())
                sc.GetGlobalSettings().SetSystemUnit(sc2.GetGlobalSettings().GetSystemUnit())
        finally:
            try:
                sdk.destroy_manager(m2)
            except Exception:
                pass

        scene_root = sc.GetRootNode()
        if scene_root is None:
            raise RuntimeError("Animation scene has no root node")

        # Ensure container under scene root (preserve transforms of any existing top ancestor like 'Actor 1')
        container_node = None
        if expected_container:
            # Find current root joint and its top ancestor under scene root, to preserve placement
            def find_by_name(n, nm: str):
                if (n.GetName() or "") == nm:
                    return n
                for i in range(n.GetChildCount()):
                    r = find_by_name(n.GetChild(i), nm)
                    if r:
                        return r
                return None
            current_root = find_by_name(scene_root, root_joint)
            top_ancestor = None
            if current_root is not None:
                p = current_root
                while p is not None and p.GetParent() is not None and p.GetParent() is not scene_root:
                    p = p.GetParent()
                top_ancestor = p

            container_node = _find_or_create_node(sc, expected_container, make_skeleton=False)
            if container_node.GetParent() is not scene_root:
                try:
                    (container_node.GetParent() or scene_root).RemoveChild(container_node)
                except Exception:
                    pass
                scene_root.AddChild(container_node)
            # If there was a top ancestor (e.g., 'Actor 1'), copy its local TRS onto the container
            if top_ancestor is not None and top_ancestor is not container_node:
                try:
                    t = top_ancestor.LclTranslation.Get(); r = top_ancestor.LclRotation.Get(); s = top_ancestor.LclScaling.Get()
                    container_node.LclTranslation.Set(t)
                    container_node.LclRotation.Set(r)
                    container_node.LclScaling.Set(s)
                except Exception:
                    pass

        # Ensure root joint exists and is under container (or scene root if none)
        root_node = _find_or_create_node(sc, root_joint, make_skeleton=True)
        desired_parent = container_node or scene_root
        if root_node.GetParent() is not desired_parent:
            try:
                (root_node.GetParent() or scene_root).RemoveChild(root_node)
            except Exception:
                pass
            desired_parent.AddChild(root_node)

        # Remove obsolete top-level ancestors like 'Actor 1' after reparenting
        def cleanup_obsolete_ancestors():
            # Any direct children of scene root that are not the container and not root_node should be pruned
            keep = {container_node, root_node}
            for i in reversed(range(scene_root.GetChildCount())):
                child = scene_root.GetChild(i)
                if child in keep:
                    continue
                name = child.GetName() or ""
                # Remove only if it has no non-skeleton children remaining
                attr = child.GetNodeAttribute()
                try:
                    is_skel = isinstance(attr, fbx.FbxSkeleton)
                except Exception:
                    is_skel = False
                if not is_skel and name not in (expected_container or ""):
                    # Detach its children to scene root (or container) then remove
                    moved_children = [child.GetChild(ci) for ci in range(child.GetChildCount())]
                    for ch in moved_children:
                        try:
                            child.RemoveChild(ch)
                            (container_node or scene_root).AddChild(ch)
                        except Exception:
                            pass
                    try:
                        scene_root.RemoveChild(child)
                    except Exception:
                        pass
        try:
            cleanup_obsolete_ancestors()
        except Exception:
            pass

        # Ensure every bone exists and has the correct parent relationship
        layer = _ensure_layer(sc)
        # Map Joint type labels to SDK enum
        type_map = {
            "Root": getattr(fbx.FbxSkeleton, "eRoot", 0),
            "Limb": getattr(fbx.FbxSkeleton, "eLimb", 2),
            "LimbNode": getattr(fbx.FbxSkeleton, "eLimbNode", 1),
            "Effector": getattr(fbx.FbxSkeleton, "eEffector", 3),
        }
        skeleton_names: Set[str] = set(parent_map.keys())
        # First, create/parent and set attributes + rest TRS
        for name, parent_name in parent_map.items():
            node = _find_or_create_node(sc, name, make_skeleton=True)
            # Set skeleton type: enforce a single Root at the skeleton root,
            # preserve Effectors for leafs, use LimbNode for all others.
            try:
                sk_attr = node.GetNodeAttribute()
                if isinstance(sk_attr, fbx.FbxSkeleton):
                    if name == root_joint:
                        sk_attr.SetSkeletonType(type_map.get("Root"))
                    else:
                        jt = joint_type_map.get(name, "LimbNode")
                        if jt == "Effector" or name.endswith("_end"):
                            sk_attr.SetSkeletonType(type_map.get("Effector"))
                        else:
                            sk_attr.SetSkeletonType(type_map.get("LimbNode"))
            except Exception:
                pass
            # Parent
            target_parent = root_node if parent_name is None else _find_or_create_node(sc, parent_name, make_skeleton=True)
            if node.GetParent() is not target_parent:
                try:
                    (node.GetParent() or scene_root).RemoveChild(node)
                except Exception:
                    pass
                target_parent.AddChild(node)
            # Copy rest TRS from skeleton
            try:
                t, r, s = rest_trs_map.get(name, ((0.0,0.0,0.0),(0.0,0.0,0.0),(1.0,1.0,1.0)))
                node.LclTranslation.Set(fbx.FbxDouble3(*t))
                node.LclRotation.Set(fbx.FbxDouble3(*r))
                node.LclScaling.Set(fbx.FbxDouble3(*s))
            except Exception:
                pass
            # Ensure curves exist at least at identity if none exist
            _ensure_identity_curves(node, layer)

        # Prune extra animated skeleton-like nodes not present in the skeleton
        def prune_extras(n: Any):
            # Do not prune scene root or container
            if n is scene_root or (container_node is not None and n is container_node):
                for i in range(n.GetChildCount()):
                    prune_extras(n.GetChild(i))
                return
            name = n.GetName() or ""
            attr = n.GetNodeAttribute()
            is_sk = False
            try:
                is_sk = isinstance(attr, fbx.FbxSkeleton)
            except Exception:
                is_sk = False
            if is_sk and name and name not in skeleton_names:
                parent = n.GetParent()
                if parent:
                    parent.RemoveChild(n)
                return
            # Recurse
            for i in range(n.GetChildCount()):
                prune_extras(n.GetChild(i))

        prune_extras(scene_root)

        # No hard-coded name removals; cleanup is handled generically above

        # Save
        _deduplicate_nodes_by_name(sc, skeleton_names)
        if not sdk.save_scene(m, sc, output_path):
            raise RuntimeError(f"Failed to save conformed animation to: {output_path}")
    finally:
        sdk.destroy_manager(m)
