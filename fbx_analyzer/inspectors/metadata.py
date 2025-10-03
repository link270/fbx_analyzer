"""Scene metadata inspector."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..core import sdk
from ..core.analyzer import SceneContext, SceneInspector
from ..models import (
    DefinitionSummary,
    FBXConnectionInfo,
    FBXPropertyEntry,
    SceneMetadata,
    SceneObjectInfo,
)


class SceneMetadataInspector(SceneInspector):
    """Collect global settings, objects, and connection details for an FBX scene."""

    id = "scene_metadata"

    def collect(self, context: SceneContext) -> SceneMetadata:
        fbx, _ = sdk.import_fbx_module()
        scene = context.scene

        metadata = SceneMetadata()
        metadata.global_settings = _collect_properties(fbx, scene.GetGlobalSettings())
        metadata.global_settings.extend(_collect_global_settings_overview(scene))

        document_info = scene.GetSceneInfo()
        if document_info is not None:
            metadata.document_info = _collect_properties(fbx, document_info)
            metadata.document_info.extend(_collect_document_info_overview(document_info))

        objects: Dict[int, SceneObjectInfo] = {}
        seen_uids: set[int] = set()

        root = scene.GetRootNode()
        if root is not None:
            _collect_node_hierarchy(fbx, root, objects, seen_uids)

        for index in range(scene.GetSrcObjectCount()):
            obj = scene.GetSrcObject(index)
            _collect_object(fbx, obj, objects, seen_uids)

        manager = context.manager
        get_object = getattr(manager, "GetObject", None)
        get_count = getattr(manager, "GetObjectCount", None)
        if callable(get_object) and callable(get_count):
            for index in range(get_count()):
                obj = get_object(index)
                _collect_object(fbx, obj, objects, seen_uids)

        metadata.objects = sorted(objects.values(), key=lambda info: (info.name or "", info.uid))

        class_counts: Dict[str, int] = defaultdict(int)
        for info in metadata.objects:
            class_counts[info.class_name] += 1
        metadata.definitions = [
            DefinitionSummary(class_name=name, object_count=count)
            for name, count in sorted(class_counts.items(), key=lambda item: item[0].lower())
        ]

        return metadata


def _collect_node_hierarchy(
    fbx_module,
    node,
    objects: Dict[int, SceneObjectInfo],
    seen_uids: set[int],
) -> None:
    _collect_object(fbx_module, node, objects, seen_uids)
    attribute = node.GetNodeAttribute()
    if attribute is not None:
        _collect_object(fbx_module, attribute, objects, seen_uids)
    for index in range(node.GetChildCount()):
        child = node.GetChild(index)
        _collect_node_hierarchy(fbx_module, child, objects, seen_uids)


def _collect_object(
    fbx_module,
    obj,
    objects: Dict[int, SceneObjectInfo],
    seen_uids: set[int],
) -> None:
    if obj is None:
        return
    uid = _safe_uid(obj)
    if uid is None or uid in seen_uids:
        return
    seen_uids.add(uid)
    objects[uid] = _build_object_info(fbx_module, obj, uid)


def _build_object_info(fbx_module, obj, uid: int) -> SceneObjectInfo:
    name = _safe_call(obj, "GetName") or ""
    class_name = _get_class_name(obj)
    type_name = _safe_call(obj, "GetTypeName") or class_name
    properties = _collect_properties(fbx_module, obj)
    src_connections = _collect_connections(obj, "src")
    dst_connections = _collect_connections(obj, "dst")

    return SceneObjectInfo(
        uid=uid,
        name=name,
        class_name=class_name,
        type_name=type_name,
        properties=properties,
        src_connections=src_connections,
        dst_connections=dst_connections,
    )


def _collect_connections(obj, direction: str) -> List[FBXConnectionInfo]:
    results: List[FBXConnectionInfo] = []
    if direction == "src":
        count = obj.GetSrcObjectCount()
        getter = obj.GetSrcObject
    else:
        count = obj.GetDstObjectCount()
        getter = obj.GetDstObject

    for index in range(count):
        linked = getter(index)
        if linked is None:
            continue
        target_uid = _safe_uid(linked)
        if target_uid is None:
            continue
        results.append(
            FBXConnectionInfo(
                direction="Outgoing" if direction == "src" else "Incoming",
                target_uid=target_uid,
                target_name=_safe_call(linked, "GetName") or "",
                target_class=_get_class_name(linked),
            )
        )
    return results


def _collect_properties(fbx_module, subject) -> List[FBXPropertyEntry]:
    entries: List[FBXPropertyEntry] = []
    get_first = getattr(subject, "GetFirstProperty", None)
    get_next = getattr(subject, "GetNextProperty", None)
    if not callable(get_first) or not callable(get_next):
        return entries

    prop = subject.GetFirstProperty()
    while prop.IsValid():
        entries.append(_build_property_entry(fbx_module, prop))
        prop = subject.GetNextProperty(prop)
    return entries


def _collect_global_settings_overview(scene) -> List[FBXPropertyEntry]:
    settings = scene.GetGlobalSettings()
    entries: List[FBXPropertyEntry] = []

    try:
        axis_system = settings.GetAxisSystem()
        axis_label = _safe_string(axis_system)
        entries.append(
            FBXPropertyEntry(name="Axis System", type_name="FbxAxisSystem", value=axis_label)
        )
    except Exception:
        pass

    try:
        system_unit = settings.GetSystemUnit()
        unit_value = ""
        scale = getattr(system_unit, "GetScaleFactor", None)
        if callable(scale):
            unit_value = f"{scale():.6f}"
        if not unit_value:
            unit_value = _safe_string(system_unit)
        entries.append(
            FBXPropertyEntry(name="System Unit", type_name="FbxSystemUnit", value=unit_value)
        )
    except Exception:
        pass

    for accessor, label in [
        ("GetDefaultCamera", "Default Camera"),
        ("GetTimeMode", "Time Mode"),
        ("GetTimeProtocol", "Time Protocol"),
        ("GetCustomFrameRate", "Custom Frame Rate"),
    ]:
        value = _call_to_string(settings, accessor)
        if value:
            entries.append(FBXPropertyEntry(name=label, type_name="Setting", value=value))

    try:
        timeline_defaults = settings.GetTimelineDefaultTimeSpan()
        start = _safe_call(timeline_defaults, "GetStart")
        stop = _safe_call(timeline_defaults, "GetStop")
        if start and stop:
            entries.append(
                FBXPropertyEntry(
                    name="Timeline Default Span",
                    type_name="FbxTimeSpan",
                    value=f"{start} -> {stop}",
                )
            )
    except Exception:
        pass

    try:
        timeline_local = settings.GetTimelineLocalTimeSpan()
        start = _safe_call(timeline_local, "GetStart")
        stop = _safe_call(timeline_local, "GetStop")
        if start and stop:
            entries.append(
                FBXPropertyEntry(
                    name="Timeline Local Span",
                    type_name="FbxTimeSpan",
                    value=f"{start} -> {stop}",
                )
            )
    except Exception:
        pass

    try:
        timeline_ref = settings.GetTimelineReferenceTimeSpan()
        start = _safe_call(timeline_ref, "GetStart")
        stop = _safe_call(timeline_ref, "GetStop")
        if start and stop:
            entries.append(
                FBXPropertyEntry(
                    name="Timeline Reference Span",
                    type_name="FbxTimeSpan",
                    value=f"{start} -> {stop}",
                )
            )
    except Exception:
        pass

    return entries


def _collect_document_info_overview(scene_info) -> List[FBXPropertyEntry]:
    fields = [
        ("GetTitle", "Title"),
        ("GetSubject", "Subject"),
        ("GetAuthor", "Author"),
        ("GetKeywords", "Keywords"),
        ("GetRevision", "Revision"),
        ("GetComment", "Comment"),
        ("GetUrl", "URL"),
        ("GetEmail", "Email"),
        ("GetDateTime", "Date"),
    ]
    entries: List[FBXPropertyEntry] = []
    for accessor, label in fields:
        value = _call_to_string(scene_info, accessor)
        if value:
            entries.append(FBXPropertyEntry(name=label, type_name="Info", value=value))
    return entries


def _build_property_entry(fbx_module, prop) -> FBXPropertyEntry:
    type_name = ""
    data_type = getattr(prop, "GetPropertyDataType", None)
    if callable(data_type):
        try:
            type_obj = data_type()
            type_name = getattr(type_obj, "GetName", lambda: str(type_obj))()
        except Exception:
            type_name = str(type_obj)

    value = _format_property_value(prop)
    flags = _collect_property_flags(fbx_module, prop)

    return FBXPropertyEntry(
        name=prop.GetName() or "",
        type_name=type_name,
        value=value,
        flags=tuple(flags),
    )


def _collect_property_flags(fbx_module, prop) -> List[str]:
    flag_labels = [
        (fbx_module.FbxPropertyFlags.eAnimatable, "Animatable"),
        (fbx_module.FbxPropertyFlags.eAnimated, "Animated"),
        (fbx_module.FbxPropertyFlags.eUserDefined, "UserDefined"),
        (fbx_module.FbxPropertyFlags.eMutable, "Mutable"),
        (fbx_module.FbxPropertyFlags.eImported, "Imported"),
    ]
    labels: List[str] = []
    for flag, label in flag_labels:
        try:
            if prop.GetFlag(flag):
                labels.append(label)
        except Exception:
            continue
    return labels


def _call_to_string(obj, accessor: str) -> str:
    func = getattr(obj, accessor, None)
    if not callable(func):
        return ""
    try:
        result = func()
    except Exception:
        return ""
    if result is None:
        return ""
    return _safe_string(result)


def _safe_string(value) -> str:
    try:
        if hasattr(value, "Buffer"):
            return str(value.Buffer())
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return "<unavailable>"


def _format_property_value(prop) -> str:
    try:
        value = prop.Get()
    except Exception:
        return "<unavailable>"

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode(errors="ignore")

    if isinstance(value, str):
        return value

    if isinstance(value, (list, tuple)):
        return ", ".join(str(component) for component in value)

    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except TypeError:
        return str(value)
    else:
        return ", ".join(str(component) for component in iterator)


def _safe_uid(obj) -> Optional[int]:
    getter = getattr(obj, "GetUniqueID", None)
    if not callable(getter):
        return None
    try:
        return int(getter())
    except Exception:
        return None


def _safe_call(obj, method: str) -> Optional[str]:
    func = getattr(obj, method, None)
    if not callable(func):
        return None
    try:
        result = func()
    except Exception:
        return None
    if result is None:
        return None
    return str(result)


def _get_class_name(obj) -> str:
    get_class_id = getattr(obj, "GetClassId", None)
    if callable(get_class_id):
        try:
            class_id = get_class_id()
            name = getattr(class_id, "GetName", None)
            if callable(name):
                class_name = name()
                if class_name:
                    return str(class_name)
        except Exception:
            pass
    return obj.__class__.__name__
