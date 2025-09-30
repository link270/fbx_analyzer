"""Extract skeleton hierarchies from FBX scenes."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

from ..core import sdk, traversal
from ..core.analyzer import SceneContext, SceneInspector
from ..models import Joint, JointTransform, Skeleton


_DEF_SKELETON_LABELS: Dict[str, str] = {
    "eRoot": "Root",
    "eLimb": "Limb",
    "eLimbNode": "LimbNode",
    "eEffector": "Effector",
}


def _resolve_enum_value(enum_holder: Any, target_name: str) -> Any:
    """Resolve an enum value from the FBX SDK Python bindings.

    Different FBX SDK releases expose enums either directly on the class or
    nested inside helper classes (e.g., `EType`). This function normalises that
    so we can write version-agnostic code.
    """

    if hasattr(enum_holder, target_name):
        return getattr(enum_holder, target_name)

    for attr_name in dir(enum_holder):
        if attr_name.lower() == target_name.lower():
            return getattr(enum_holder, attr_name)

    nested = getattr(enum_holder, "EType", None)
    if nested:
        for attr_name in dir(nested):
            if attr_name.lower() == target_name.lower():
                return getattr(nested, attr_name)

    raise AttributeError(f"Unable to resolve enum value '{target_name}' from {enum_holder!r}")


def _build_skeleton_type_labels(fbx_module) -> Dict[Any, str]:
    labels: Dict[Any, str] = {}
    for key, caption in _DEF_SKELETON_LABELS.items():
        try:
            enum_value = _resolve_enum_value(fbx_module.FbxSkeleton, key)
        except AttributeError:
            continue
        labels[enum_value] = caption
    return labels


def _double3_to_tuple(vector) -> tuple[float, float, float]:
    return (float(vector[0]), float(vector[1]), float(vector[2]))


class SkeletonInspector(SceneInspector):
    id = "skeletons"

    def collect(self, context: SceneContext) -> List[Skeleton]:
        fbx, _ = sdk.import_fbx_module()

        skeleton_type_labels = _build_skeleton_type_labels(fbx)
        explicit = _collect_explicit_skeletons(context, fbx, skeleton_type_labels)
        if explicit:
            return explicit

        inferred = _collect_animation_bound_skeletons(context, fbx)
        return inferred


def _collect_explicit_skeletons(
    context: SceneContext, fbx_module, skeleton_type_labels: Dict[Any, str]
) -> List[Skeleton]:
    skeleton_class_id = getattr(fbx_module.FbxSkeleton, "ClassId", None)
    if skeleton_class_id is None:
        return []

    def is_skeleton_node(node) -> bool:
        attribute = node.GetNodeAttribute()
        return bool(attribute and attribute.GetClassId().Is(skeleton_class_id))

    def classify_type(skeleton_attr) -> str:
        if not skeleton_attr:
            return "Node"
        skeleton_type = skeleton_attr.GetSkeletonType()
        return skeleton_type_labels.get(skeleton_type, f"Unknown({skeleton_type})")

    def to_joint(node, parent_name=None) -> Joint:
        attribute = node.GetNodeAttribute()
        skeleton_attr = attribute if attribute and attribute.GetClassId().Is(skeleton_class_id) else None
        joint_type = classify_type(skeleton_attr)

        joint = Joint(
            name=node.GetName() or f"Node_{node.GetUniqueID()}",
            joint_type=joint_type,
            transform=JointTransform(
                translation=_double3_to_tuple(node.LclTranslation.Get()),
                rotation=_double3_to_tuple(node.LclRotation.Get()),
                scaling=_double3_to_tuple(node.LclScaling.Get()),
            ),
            parent_name=parent_name,
        )

        for child_idx in range(node.GetChildCount()):
            child = node.GetChild(child_idx)
            if is_skeleton_node(child):
                joint.children.append(to_joint(child, parent_name=joint.name))

        return joint

    skeleton_roots: List[Skeleton] = []
    for node in traversal.iter_nodes(context.root_node):
        if not is_skeleton_node(node):
            continue

        parent = node.GetParent()
        if parent and is_skeleton_node(parent):
            continue  # Only collect top-level skeleton nodes.

        skeleton_name = node.GetName() or "SkeletonRoot"
        skeleton_roots.append(Skeleton(name=skeleton_name, root=to_joint(node)))

    return skeleton_roots


def _collect_animation_bound_skeletons(context: SceneContext, fbx_module) -> List[Skeleton]:
    layers = _collect_anim_layers(context.scene, fbx_module)
    cluster_uids = _collect_cluster_link_nodes(context.root_node, fbx_module)

    node_lookup: Dict[int, Any] = {}
    animated_uids: Set[int] = set()

    for node in traversal.iter_nodes(context.root_node):
        uid = node.GetUniqueID()
        node_lookup[uid] = node
        if layers and _node_has_animation(node, layers):
            animated_uids.add(uid)

    candidate_uids = set(cluster_uids) | animated_uids
    expanded_uids = set(candidate_uids)
    for uid in list(candidate_uids):
        node = node_lookup.get(uid)
        current = node.GetParent() if node else None
        while current:
            current_uid = current.GetUniqueID()
            if current_uid in expanded_uids:
                break
            expanded_uids.add(current_uid)
            current = current.GetParent()
    candidate_uids = expanded_uids
    if not candidate_uids:
        return []

    visited: Set[int] = set()
    skeletons: List[Skeleton] = []

    for uid in candidate_uids:
        if uid in visited:
            continue
        node = node_lookup.get(uid)
        if node is None:
            continue
        parent = node.GetParent()
        if parent and parent.GetUniqueID() in candidate_uids:
            continue

        root_joint = _build_fallback_joint(
            node,
            candidate_uids,
            cluster_uids,
            animated_uids,
            visited,
            parent_name=None,
        )
        skeleton_name = node.GetName() or "AnimatedRig"
        skeletons.append(Skeleton(name=skeleton_name, root=root_joint))

    return skeletons


def _collect_anim_layers(scene, fbx_module) -> List[Any]:
    layers: List[Any] = []

    def iter_objects(container, class_id):
        get_count = getattr(container, "GetSrcObjectCount", None)
        get_object = getattr(container, "GetSrcObject", None)
        if not callable(get_count) or not callable(get_object):
            return []

        results = []

        try:
            criteria = fbx_module.FbxCriteria.ObjectType(class_id)
            count = get_count(criteria)
            for idx in range(count):
                try:
                    candidate = get_object(criteria, idx)
                except TypeError:
                    continue
                if candidate and hasattr(candidate, "GetClassId") and candidate.GetClassId().Is(class_id):
                    results.append(candidate)
            if results:
                return results
        except Exception:
            pass

        try:
            count = get_count()
        except TypeError:
            try:
                count = get_count(0)
            except Exception:
                return []

        for idx in range(count or 0):
            try:
                candidate = get_object(idx)
            except TypeError:
                try:
                    candidate = get_object(class_id, idx)
                except Exception:
                    continue
            if candidate and hasattr(candidate, "GetClassId") and candidate.GetClassId().Is(class_id):
                results.append(candidate)
        return results

    try:
        for layer in iter_objects(scene, fbx_module.FbxAnimLayer.ClassId):
            layers.append(layer)
    except Exception:
        pass

    if layers:
        return layers

    for stack in iter_objects(scene, fbx_module.FbxAnimStack.ClassId):
        for layer in iter_objects(stack, fbx_module.FbxAnimLayer.ClassId):
            layers.append(layer)

    return layers

def _node_has_animation(node, layers: Iterable[Any]) -> bool:
    properties = (node.LclTranslation, node.LclRotation, node.LclScaling)
    for layer in layers:
        for prop in properties:
            try:
                curve_node = prop.GetCurveNode(layer)
            except AttributeError:
                continue
            if curve_node:
                return True
    return False


def _collect_cluster_link_nodes(root_node, fbx_module) -> Set[int]:
    cluster_uids: Set[int] = set()

    mesh_class_id = getattr(fbx_module.FbxMesh, "ClassId", None)
    mesh_attribute_type = getattr(fbx_module.FbxNodeAttribute, "eMesh", None)
    skin_enum = getattr(fbx_module.FbxDeformer, "eSkin", None)

    for node in traversal.iter_nodes(root_node):
        attribute = node.GetNodeAttribute()
        if attribute is None:
            continue

        is_mesh = False
        try:
            if mesh_class_id and attribute.GetClassId().Is(mesh_class_id):
                is_mesh = True
        except AttributeError:
            pass

        if not is_mesh and mesh_attribute_type is not None and hasattr(attribute, "GetAttributeType"):
            try:
                is_mesh = attribute.GetAttributeType() == mesh_attribute_type
            except Exception:
                is_mesh = False

        if not is_mesh:
            continue

        if skin_enum is None:
            continue

        try:
            deformer_count = attribute.GetDeformerCount(skin_enum)
        except AttributeError:
            continue

        for deformer_index in range(deformer_count):
            try:
                skin = attribute.GetDeformer(deformer_index, skin_enum)
            except AttributeError:
                skin = None
            if not skin:
                continue
            cluster_count = skin.GetClusterCount()
            for cluster_index in range(cluster_count):
                cluster = skin.GetCluster(cluster_index)
                if not cluster:
                    continue
                link = cluster.GetLink()
                if link:
                    cluster_uids.add(link.GetUniqueID())

    return cluster_uids


def _build_fallback_joint(
    node,
    candidate_uids: Set[int],
    cluster_uids: Set[int],
    animated_uids: Set[int],
    visited: Set[int],
    parent_name: str | None,
) -> Joint:
    uid = node.GetUniqueID()
    visited.add(uid)

    joint = Joint(
        name=node.GetName() or f"Node_{uid}",
        joint_type=_classify_fallback_joint(uid, cluster_uids, animated_uids),
        transform=JointTransform(
            translation=_double3_to_tuple(node.LclTranslation.Get()),
            rotation=_double3_to_tuple(node.LclRotation.Get()),
            scaling=_double3_to_tuple(node.LclScaling.Get()),
        ),
        parent_name=parent_name,
    )

    for child_idx in range(node.GetChildCount()):
        child = node.GetChild(child_idx)
        child_uid = child.GetUniqueID()
        if child_uid in candidate_uids and child_uid not in visited:
            joint.children.append(
                _build_fallback_joint(
                    child,
                    candidate_uids,
                    cluster_uids,
                    animated_uids,
                    visited,
                    parent_name=joint.name,
                )
            )

    return joint


def _classify_fallback_joint(uid: int, cluster_uids: Set[int], animated_uids: Set[int]) -> str:
    in_cluster = uid in cluster_uids
    in_animation = uid in animated_uids

    if in_cluster and in_animation:
        return "AnimatedCluster"
    if in_cluster:
        return "ClusterLink"
    if in_animation:
        return "AnimatedNode"
    return "Node"
