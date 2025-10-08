from __future__ import annotations

from typing import Optional, Tuple

from ..core import sdk


def _find_node_by_name(root, name: str):  # type: ignore[valid-type]
    if not root:
        return None
    if (root.GetName() or "") == name:
        return root
    for i in range(root.GetChildCount()):
        res = _find_node_by_name(root.GetChild(i), name)
        if res is not None:
            return res
    return None


def _get_expected_root_name_from_skeleton(skel_scene) -> Tuple[str, Optional[str]]:  # type: ignore[valid-type]
    """Return (skeleton_root_joint_name, expected_container_root_name).

    If the skeleton root joint has a parent that is not the SDK scene root, we
    treat that parent name as the expected root container name for UE.
    """

    root = skel_scene.GetRootNode()
    if root is None:
        raise RuntimeError("Skeleton scene has no root node")

    # Heuristic: find first node that has a Skeleton attribute and no parent skeleton
    fbx, _ = sdk.import_fbx_module()
    skel_class = getattr(fbx, "FbxSkeleton", None)
    if skel_class is None:
        raise RuntimeError("FbxSkeleton class not available in SDK")

    def is_skeleton(n) -> bool:
        a = n.GetNodeAttribute()
        try:
            return bool(a and isinstance(a, skel_class))
        except Exception:
            return False

    def find_skeleton_root(n):
        for i in range(n.GetChildCount()):
            c = n.GetChild(i)
            if is_skeleton(c):
                # ensure parent is not a skeleton
                p = c.GetParent()
                if not (p and is_skeleton(p)):
                    return c
            res = find_skeleton_root(c)
            if res is not None:
                return res
        return None

    skel_root = find_skeleton_root(root)
    if skel_root is None:
        raise RuntimeError("Unable to locate skeleton root joint in skeleton scene")

    parent = skel_root.GetParent()
    expected_container = None
    if parent is not None and parent != root:
        expected_container = parent.GetName() or None
    return (skel_root.GetName() or "", expected_container)


def add_missing_root_track(
    skeleton_path: str, animation_path: str, output_path: str
) -> None:
    """Patch the animation FBX to include a constant track for the skeleton's UE root.

    If the skeleton root joint (e.g., 'Hips') has a non-root parent (e.g.,
    'exampleCharacter_BODY'), the animation file will receive a new node with
    that parent name inserted above the animated skeleton root (if missing) and
    identity TRS curves will be added for it.
    """

    fbx, _ = sdk.import_fbx_module()

    # Load skeleton and get expected root names
    m_skel = sdk.create_manager()
    try:
        sdk.create_io_settings(m_skel)
        sc_skel = sdk.create_scene(m_skel)
        if not sdk.load_scene(m_skel, sc_skel, skeleton_path):
            raise RuntimeError(f"Failed to load skeleton FBX: {skeleton_path}")
        skel_root_joint, expected_container = _get_expected_root_name_from_skeleton(sc_skel)
    finally:
        sdk.destroy_manager(m_skel)

    # Load animation
    m = sdk.create_manager()
    try:
        sdk.create_io_settings(m)
        sc = sdk.create_scene(m)
        if not sdk.load_scene(m, sc, animation_path):
            raise RuntimeError(f"Failed to load animation FBX: {animation_path}")

        # Align global units/axis from skeleton when possible
        # (Keep it minimal: only copy when APIs are available)
        try:
            m2 = sdk.create_manager()
            try:
                sdk.create_io_settings(m2)
                sc2 = sdk.create_scene(m2)
                if sdk.load_scene(m2, sc2, skeleton_path):
                    sc.GetGlobalSettings().SetAxisSystem(sc2.GetGlobalSettings().GetAxisSystem())
                    sc.GetGlobalSettings().SetSystemUnit(sc2.GetGlobalSettings().GetSystemUnit())
            finally:
                sdk.destroy_manager(m2)
        except Exception:
            pass

        scene_root = sc.GetRootNode()
        if scene_root is None:
            raise RuntimeError("Animation scene has no root node")

        # Locate the animated skeleton root joint by name
        animated_root = _find_node_by_name(scene_root, skel_root_joint)
        if animated_root is None:
            # As a fallback, patch directly at scene root if we can't find the joint
            animated_root = scene_root

        # Ensure expected container exists and sits above animated_root
        target_name = expected_container or skel_root_joint
        target_node = _find_node_by_name(scene_root, target_name)
        # Preserve transforms from top ancestor like 'Actor 1'
        def find_by_name(n, nm: str):
            if (n.GetName() or "") == nm:
                return n
            for i in range(n.GetChildCount()):
                r = find_by_name(n.GetChild(i), nm)
                if r:
                    return r
            return None
        top_ancestor = None
        curr_root = find_by_name(scene_root, skel_root_joint)
        if curr_root is not None:
            p = curr_root
            while p is not None and p.GetParent() is not None and p.GetParent() is not scene_root:
                p = p.GetParent()
            top_ancestor = p

        if target_node is None:
            target_node = fbx.FbxNode.Create(sc, target_name)
            scene_root.AddChild(target_node)
        # Ensure expected container sits directly under scene root to mirror typical skeleton structure
        if target_node.GetParent() is not scene_root:
            try:
                # Detach from current parent and attach to scene root
                (target_node.GetParent() or scene_root).RemoveChild(target_node)
            except Exception:
                pass
            scene_root.AddChild(target_node)
        # Make container a Null and copy transforms from previous top ancestor
        try:
            null_attr = fbx.FbxNull.Create(sc, target_name or "Null")
            target_node.SetNodeAttribute(null_attr)
        except Exception:
            pass
        if top_ancestor is not None and top_ancestor is not target_node:
            try:
                t = top_ancestor.LclTranslation.Get(); r = top_ancestor.LclRotation.Get()
                target_node.LclTranslation.Set(t)
                target_node.LclRotation.Set(r)
                # Avoid inheriting bad zero scale from sources; use identity scale for container
                target_node.LclScaling.Set(fbx.FbxDouble3(1.0,1.0,1.0))
            except Exception:
                pass
        # Reparent the animated root under the target container
        if animated_root.GetParent() is not target_node:
            try:
                (animated_root.GetParent() or scene_root).RemoveChild(animated_root)
            except Exception:
                pass
            target_node.AddChild(animated_root)

        # If the previous top ancestor becomes empty after reparent, remove it generically
        try:
            if top_ancestor is not None and top_ancestor is not target_node:
                # Move remaining children (if any) to container, then remove ancestor
                moved_children = [top_ancestor.GetChild(i) for i in range(top_ancestor.GetChildCount())]
                for ch in moved_children:
                    try:
                        top_ancestor.RemoveChild(ch)
                        target_node.AddChild(ch)
                    except Exception:
                        pass
                parent = top_ancestor.GetParent()
                if parent:
                    try:
                        parent.RemoveChild(top_ancestor)
                    except Exception:
                        pass
        except Exception:
            pass

        # Ensure an anim stack/layer exists
        stack = None
        try:
            crit = fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId)
            cnt = sc.GetSrcObjectCount(crit)
            if cnt > 0:
                stack = sc.GetSrcObject(crit, 0)
        except Exception:
            stack = None
        if stack is None:
            stack = fbx.FbxAnimStack.Create(sc, "Stack")
        layer = None
        try:
            if hasattr(stack, "GetMemberCount") and stack.GetMemberCount() > 0:
                layer = stack.GetMember(0)
        except Exception:
            layer = None
        if layer is None:
            layer = fbx.FbxAnimLayer.Create(sc, "BaseLayer")
            try:
                stack.AddMember(layer)
            except Exception:
                pass

        # Add constant identity curves on the target node if none exist
        def _ensure_curve(prop, component: str, value: float):
            try:
                curve = prop.GetCurve(layer, component, True)
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
                    curve.KeySetValue(idx, value)
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
            _ensure_curve(target_node.LclTranslation, comp, val)
            _ensure_curve(target_node.LclRotation, comp, val)
        for comp, val in (("X", 1.0), ("Y", 1.0), ("Z", 1.0)):
            _ensure_curve(target_node.LclScaling, comp, val)

        # Save patched animation
        if not sdk.save_scene(m, sc, output_path):
            raise RuntimeError(f"Failed to save patched animation to: {output_path}")
    finally:
        sdk.destroy_manager(m)
