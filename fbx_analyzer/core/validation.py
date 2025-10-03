"""Scene validation and auto-repair helpers for FBX exports."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import sdk


Severity = str


def _severity_order(value: Severity) -> int:
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    return order.get(value, 2)


@dataclass
class ValidationIssue:
    """Represents a single validation problem or note."""

    severity: Severity
    message: str
    code: str
    object_path: Optional[str] = None
    fix_applied: Optional[str] = None


@dataclass
class ValidationCategoryReport:
    """Aggregated issues for a validation category."""

    name: str
    issues: List[ValidationIssue] = field(default_factory=list)

    def add_issue(
        self,
        severity: Severity,
        message: str,
        code: str,
        *,
        object_path: Optional[str] = None,
        fix_applied: Optional[str] = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                severity=severity,
                message=message,
                code=code,
                object_path=object_path,
                fix_applied=fix_applied,
            )
        )

    @property
    def status(self) -> Severity:
        if not self.issues:
            return "PASS"
        return max((issue.severity for issue in self.issues), key=_severity_order)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "issues": [issue.__dict__ for issue in self.issues],
        }


@dataclass
class MeshMetrics:
    control_points: int
    polygon_count: int
    layer_elements: Dict[str, int] = field(default_factory=dict)


@dataclass
class SceneMetrics:
    node_count: int = 0
    mesh_metrics: Dict[str, MeshMetrics] = field(default_factory=dict)
    material_count: int = 0
    texture_count: int = 0
    skin_cluster_count: int = 0
    bind_pose_count: int = 0
    anim_stack_count: int = 0
    anim_curve_count: int = 0

    def diff(self, other: "SceneMetrics") -> List[Dict[str, Any]]:
        """Return metric differences compared to ``other``."""

        differences: List[Dict[str, Any]] = []

        def record(label: str, expected: Any, actual: Any) -> None:
            differences.append(
                {
                    "metric": label,
                    "expected": expected,
                    "actual": actual,
                }
            )

        if self.node_count != other.node_count:
            record("node_count", other.node_count, self.node_count)

        if self.material_count != other.material_count:
            record("material_count", other.material_count, self.material_count)

        if self.texture_count != other.texture_count:
            record("texture_count", other.texture_count, self.texture_count)

        if self.skin_cluster_count != other.skin_cluster_count:
            record("skin_cluster_count", other.skin_cluster_count, self.skin_cluster_count)

        if self.bind_pose_count != other.bind_pose_count:
            record("bind_pose_count", other.bind_pose_count, self.bind_pose_count)

        if self.anim_stack_count != other.anim_stack_count:
            record("anim_stack_count", other.anim_stack_count, self.anim_stack_count)

        if self.anim_curve_count != other.anim_curve_count:
            record("anim_curve_count", other.anim_curve_count, self.anim_curve_count)

        mesh_keys = set(self.mesh_metrics) | set(other.mesh_metrics)
        for key in sorted(mesh_keys):
            lhs = self.mesh_metrics.get(key)
            rhs = other.mesh_metrics.get(key)
            if lhs is None or rhs is None:
                record(f"mesh:{key}", rhs.__dict__ if rhs else None, lhs.__dict__ if lhs else None)
                continue
            if lhs.control_points != rhs.control_points:
                record(f"mesh:{key}:control_points", rhs.control_points, lhs.control_points)
            if lhs.polygon_count != rhs.polygon_count:
                record(f"mesh:{key}:polygon_count", rhs.polygon_count, lhs.polygon_count)
            layer_keys = set(lhs.layer_elements) | set(rhs.layer_elements)
            for layer_key in sorted(layer_keys):
                left_val = lhs.layer_elements.get(layer_key)
                right_val = rhs.layer_elements.get(layer_key)
                if left_val != right_val:
                    record(f"mesh:{key}:layer:{layer_key}", right_val, left_val)

        return differences


@dataclass
class ValidationReport:
    """Aggregated validation summary for a scene."""

    categories: Dict[str, ValidationCategoryReport] = field(default_factory=dict)
    repairs: List[Dict[str, str]] = field(default_factory=list)
    metrics: SceneMetrics = field(default_factory=SceneMetrics)

    @property
    def export_ready(self) -> bool:
        return all(category.status != "FAIL" for category in self.categories.values())

    def status_summary(self) -> Dict[str, Severity]:
        return {name: category.status for name, category in self.categories.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "categories": {name: category.to_dict() for name, category in self.categories.items()},
            "repairs": list(self.repairs),
            "metrics": {
                "node_count": self.metrics.node_count,
                "mesh_metrics": {
                    key: {
                        "control_points": value.control_points,
                        "polygon_count": value.polygon_count,
                        "layer_elements": dict(value.layer_elements),
                    }
                    for key, value in self.metrics.mesh_metrics.items()
                },
                "material_count": self.metrics.material_count,
                "texture_count": self.metrics.texture_count,
                "skin_cluster_count": self.metrics.skin_cluster_count,
                "bind_pose_count": self.metrics.bind_pose_count,
                "anim_stack_count": self.metrics.anim_stack_count,
                "anim_curve_count": self.metrics.anim_curve_count,
            },
        }


@dataclass
class RoundTripDiffReport:
    validation: ValidationReport
    metrics_diff: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation": self.validation.to_dict(),
            "metrics_diff": list(self.metrics_diff),
        }


@dataclass
class CanonicalSettings:
    """Canonical global settings used for repair."""

    axis_system: Any
    system_unit: Any
    time_mode: Any
    frame_rate: float
    time_span: Optional[Tuple[int, int]] = None

    @classmethod
    def default(cls) -> "CanonicalSettings":
        fbx, _ = sdk.import_fbx_module()
        axis_system = getattr(fbx.FbxAxisSystem, "MayaYUp", None)
        system_unit = getattr(fbx.FbxSystemUnit, "cm", None)

        time_mode = None
        time_class = getattr(fbx, "FbxTime", None)
        time_enum = getattr(fbx, "FbxTimeMode", None)

        if time_class is not None:
            time_mode = getattr(time_class, "eFrames30", None)
        if time_mode is None and time_enum is not None:
            time_mode = getattr(time_enum, "eFrames30", None)
        if time_mode is None and time_class is not None:
            time_mode = getattr(time_class, "eDefaultMode", None)
        if time_mode is None and time_enum is not None:
            time_mode = getattr(time_enum, "eDefaultMode", None)

        frame_rate = 30.0
        if (
            time_class is not None
            and hasattr(time_class, "GetFrameRate")
            and time_mode is not None
        ):
            try:
                frame_rate = float(time_class.GetFrameRate(time_mode))
            except Exception:  # pragma: no cover - defensive fallback
                frame_rate = 30.0
        return cls(
            axis_system=axis_system,
            system_unit=system_unit,
            time_mode=time_mode,
            frame_rate=frame_rate,
            time_span=None,
        )


class SceneValidator:
    """Validator that audits a scene and applies auto-repairs when required."""

    def __init__(
        self,
        scene: Any,
        *,
        canonical_settings: Optional[CanonicalSettings] = None,
    ) -> None:
        self.scene = scene
        self.fbx, _ = sdk.import_fbx_module()
        self.canonical = canonical_settings or CanonicalSettings.default()

    # Public API ---------------------------------------------------------
    def validate(self) -> ValidationReport:
        report = ValidationReport()
        if self.canonical.time_span is None:
            global_settings = self.scene.GetGlobalSettings()
            get_default_span = getattr(global_settings, "GetTimelineDefaultTimeSpan", None)
            if callable(get_default_span):
                time_span = None
                try:
                    time_span = get_default_span()
                except TypeError:
                    time_span = self.fbx.FbxTimeSpan()
                    get_default_span(time_span)
                if time_span is not None:
                    start = time_span.GetStart().Get()
                    stop = time_span.GetStop().Get()
                    if start < stop:
                        self.canonical.time_span = (start, stop)
        report.categories["globals"] = ValidateGlobals(self.scene, self.canonical, self.fbx)
        report.categories["nodes"] = ValidateNodesAndTransforms(self.scene, self.fbx)
        geometry_report, mesh_metrics = ValidateGeometry(self.scene, self.fbx)
        report.categories["geometry"] = geometry_report
        report.categories["skin"] = ValidateSkinAndPoses(self.scene, self.fbx)
        report.categories["materials"] = ValidateMaterialsAndTextures(self.scene, self.fbx)
        report.categories["animation"] = ValidateAnimation(self.scene, self.fbx)
        report.categories["constraints"] = ValidateConstraints(self.scene, self.fbx)
        report.categories["connections"] = ValidateConnections(self.scene, self.fbx)

        report.metrics = collect_scene_metrics(self.scene, self.fbx, mesh_metrics)
        return report

    def auto_repair(self, report: ValidationReport) -> None:
        AutoRepair(report, self.scene, self.canonical, self.fbx)


# ---------------------------------------------------------------------------
# Validators


def ValidateGlobals(scene, canonical: CanonicalSettings, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("GlobalSettings")
    globals_settings = scene.GetGlobalSettings()

    axis = globals_settings.GetAxisSystem()
    canonical_axis = canonical.axis_system
    if canonical_axis is not None and hasattr(axis, "IsEquivalent"):
        if not axis.IsEquivalent(canonical_axis):
            report.add_issue(
                "FAIL",
                "Axis system does not match canonical settings.",
                code="globals.axis",
                object_path="<globals>",
            )

    system_unit = globals_settings.GetSystemUnit()
    canonical_unit = canonical.system_unit
    if canonical_unit is not None:
        try:
            current_scale = system_unit.GetScaleFactor()
            canonical_scale = canonical_unit.GetScaleFactor()
            if not math.isclose(current_scale, canonical_scale, rel_tol=1e-6):
                report.add_issue(
                    "FAIL",
                    "System unit scale mismatch.",
                    code="globals.system_unit",
                    object_path="<globals>",
                )
        except AttributeError:
            report.add_issue(
                "WARN",
                "System unit information unavailable; unable to verify.",
                code="globals.system_unit_unknown",
                object_path="<globals>",
            )

    time_mode = globals_settings.GetTimeMode()
    if canonical.time_mode is not None and time_mode != canonical.time_mode:
        report.add_issue(
            "FAIL",
            "Time mode does not match canonical export configuration.",
            code="globals.time_mode",
            object_path="<globals>",
        )

    custom_rate = None
    get_custom_rate = getattr(globals_settings, "GetCustomFrameRate", None)
    if callable(get_custom_rate):
        try:
            custom_rate = get_custom_rate()
        except TypeError:  # pragma: no cover - defensive for old SDKs
            # Some SDK builds expose GetCustomFrameRate with property semantics;
            # fall back to reading the attribute directly if invocation fails.
            custom_rate = getattr(globals_settings, "CustomFrameRate", None)
    elif hasattr(fbx_module.FbxTime, "GetFrameRate"):
        try:
            custom_rate = fbx_module.FbxTime.GetFrameRate(time_mode)
        except Exception:  # pragma: no cover - defensive
            custom_rate = None

    if canonical.time_mode == getattr(fbx_module.FbxTime, "eCustom", None):
        if custom_rate is None:
            report.add_issue(
                "WARN",
                "Custom frame rate unavailable; unable to verify.",
                code="globals.frame_rate_unknown",
                object_path="<globals>",
            )
        elif not math.isclose(custom_rate, canonical.frame_rate, rel_tol=1e-6):
            report.add_issue(
                "FAIL",
                "Custom frame rate does not match canonical export configuration.",
                code="globals.frame_rate",
                object_path="<globals>",
            )

    time_span = fbx_module.FbxTimeSpan()
    timeline_getter = getattr(globals_settings, "GetTimelineDefaultTimeSpan", None)
    if callable(timeline_getter):
        try:
            result = timeline_getter(time_span)
            # Some SDK variants return the span instead of filling the arg; prefer it.
            if isinstance(result, fbx_module.FbxTimeSpan):
                time_span = result
        except TypeError:
            # Older python bindings expose the no-arg overload returning the span.
            result = timeline_getter()
            if isinstance(result, fbx_module.FbxTimeSpan):
                time_span = result
    else:  # pragma: no cover - defensive fallback
        report.add_issue(
            "WARN",
            "Timeline default time span accessor unavailable; unable to validate span.",
            code="globals.time_span_unknown",
            object_path="<globals>",
        )
        return report

    start = time_span.GetStart().Get()
    stop = time_span.GetStop().Get()
    if start >= stop:
        report.add_issue(
            "FAIL",
            "Global time span is invalid (start >= stop).",
            code="globals.time_span",
            object_path="<globals>",
        )

    return report


def ValidateNodesAndTransforms(scene, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("NodesAndTransforms")
    root = scene.GetRootNode()
    if root is None:
        report.add_issue("FAIL", "Scene has no root node.", code="nodes.missing_root", object_path="/")
        return report

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    for node in iter_nodes(root):
        attr = node.GetNodeAttribute()
        if node != root and attr is None:
            report.add_issue(
                "WARN",
                "Node has no attribute; downstream tools may ignore it.",
                code="nodes.missing_attribute",
                object_path=_node_path(node),
            )

        try:
            node.LclTranslation.Get()
            node.LclRotation.Get()
            node.LclScaling.Get()
        except Exception:  # pragma: no cover - defensive
            report.add_issue(
                "FAIL",
                "Failed to read local transforms for node.",
                code="nodes.transform_read",
                object_path=_node_path(node),
            )

    return report


def ValidateGeometry(scene, fbx_module) -> Tuple[ValidationCategoryReport, Dict[str, MeshMetrics]]:  # type: ignore[valid-type]
    report = ValidationCategoryReport("Geometry")
    mesh_metrics: Dict[str, MeshMetrics] = {}

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    root = scene.GetRootNode()
    if root is None:
        return report, mesh_metrics

    for node in iter_nodes(root):
        attr = node.GetNodeAttribute()
        if attr is None or not isinstance(attr, fbx_module.FbxMesh):
            continue
        mesh: Any = attr
        path = _node_path(node)
        control_points = mesh.GetControlPointsCount()
        polygons = mesh.GetPolygonCount()
        layer_counts: Dict[str, int] = {}

        if control_points <= 0:
            report.add_issue(
                "FAIL",
                "Mesh has no control points.",
                code="geometry.control_points",
                object_path=path,
            )
        if polygons <= 0:
            report.add_issue(
                "FAIL",
                "Mesh has no polygons.",
                code="geometry.polygons",
                object_path=path,
            )

        layer_count = mesh.GetLayerCount()
        for layer_index in range(layer_count):
            layer = mesh.GetLayer(layer_index)
            if layer is None:
                continue
            normals = layer.GetNormals()
            if normals is not None:
                _validate_layer_element(report, normals, "Normals", path, fbx_module)
                layer_counts[f"normals:{layer_index}"] = normals.GetDirectArray().GetCount()
            tangents = layer.GetTangents()
            if tangents is not None:
                _validate_layer_element(report, tangents, "Tangents", path, fbx_module)
                layer_counts[f"tangents:{layer_index}"] = tangents.GetDirectArray().GetCount()
            binormals = layer.GetBinormals()
            if binormals is not None:
                _validate_layer_element(report, binormals, "Binormals", path, fbx_module)
                layer_counts[f"binormals:{layer_index}"] = binormals.GetDirectArray().GetCount()
            for uv_index in range(layer.GetUVSetCount()):
                uv_element = layer.GetUVSet(uv_index)
                if uv_element is None:
                    continue
                _validate_layer_element(report, uv_element, f"UVSet[{uv_index}]", path, fbx_module)
                layer_counts[f"uv{uv_index}:{layer_index}"] = uv_element.GetDirectArray().GetCount()
            vertex_colors = layer.GetVertexColors()
            if vertex_colors is not None:
                _validate_layer_element(report, vertex_colors, "VertexColors", path, fbx_module)
                layer_counts[f"vcolor:{layer_index}"] = vertex_colors.GetDirectArray().GetCount()
            smoothing = layer.GetSmoothing()
            if smoothing is not None:
                _validate_layer_element(report, smoothing, "Smoothing", path, fbx_module)
                layer_counts[f"smoothing:{layer_index}"] = smoothing.GetDirectArray().GetCount()
            materials = layer.GetMaterials()
            if materials is not None:
                _validate_layer_element(report, materials, "Materials", path, fbx_module)
                layer_counts[f"materials:{layer_index}"] = materials.GetDirectArray().GetCount()

        mesh_metrics[path] = MeshMetrics(
            control_points=control_points,
            polygon_count=polygons,
            layer_elements=layer_counts,
        )

    return report, mesh_metrics


def ValidateSkinAndPoses(scene, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("SkinningAndPoses")
    root = scene.GetRootNode()
    if root is None:
        return report

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    for node in iter_nodes(root):
        attr = node.GetNodeAttribute()
        if attr is None or not isinstance(attr, fbx_module.FbxMesh):
            continue
        mesh: Any = attr
        path = _node_path(node)
        skin_count = mesh.GetDeformerCount(fbx_module.FbxDeformer.eSkin)
        for skin_index in range(skin_count):
            skin = mesh.GetDeformer(skin_index, fbx_module.FbxDeformer.eSkin)
            if skin is None:
                continue
            cluster_count = skin.GetClusterCount()
            if cluster_count == 0:
                report.add_issue(
                    "FAIL",
                    "Skin deformer has no clusters.",
                    code="skin.no_clusters",
                    object_path=path,
                )
                continue
            for cluster_index in range(cluster_count):
                cluster = skin.GetCluster(cluster_index)
                if cluster is None:
                    continue
                link = cluster.GetLink()
                if link is None:
                    report.add_issue(
                        "FAIL",
                        "Skin cluster missing joint link.",
                        code="skin.cluster_link",
                        object_path=path,
                    )
                if cluster.GetControlPointIndicesCount() == 0 or cluster.GetControlPointWeightsCount() == 0:
                    report.add_issue(
                        "FAIL",
                        "Skin cluster has empty weights.",
                        code="skin.cluster_weights",
                        object_path=path,
                    )
                matrix = fbx_module.FbxAMatrix()
                link_matrix = fbx_module.FbxAMatrix()
                if not cluster.GetTransformMatrix(matrix):
                    report.add_issue(
                        "FAIL",
                        "Skin cluster missing transform matrix.",
                        code="skin.cluster_matrix",
                        object_path=path,
                    )
                if not cluster.GetTransformLinkMatrix(link_matrix):
                    report.add_issue(
                        "FAIL",
                        "Skin cluster missing link matrix.",
                        code="skin.cluster_link_matrix",
                        object_path=path,
                    )

    bind_pose_found = False
    for pose_index in range(scene.GetPoseCount()):
        pose = scene.GetPose(pose_index)
        if pose.IsBindPose():
            bind_pose_found = True
            if pose.GetCount() == 0:
                report.add_issue(
                    "FAIL",
                    "Bind pose has no nodes.",
                    code="skin.bind_pose_empty",
                    object_path="<poses>",
                )
    if not bind_pose_found:
        report.add_issue(
            "FAIL",
            "No bind pose present in scene.",
            code="skin.bind_pose_missing",
            object_path="<poses>",
        )

    return report


def ValidateMaterialsAndTextures(scene, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("MaterialsAndTextures")
    root = scene.GetRootNode()
    if root is None:
        return report

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    for node in iter_nodes(root):
        attr = node.GetNodeAttribute()
        if attr is None or not isinstance(attr, fbx_module.FbxMesh):
            continue
        mesh: Any = attr
        path = _node_path(node)
        material_count = node.GetMaterialCount()
        if material_count == 0 and mesh.GetElementMaterialCount() > 0:
            report.add_issue(
                "FAIL",
                "Mesh has material layer but node has no materials assigned.",
                code="materials.node_assignment",
                object_path=path,
            )

        for material_index in range(node.GetMaterialCount()):
            material = node.GetMaterial(material_index)
            if material is None:
                report.add_issue(
                    "FAIL",
                    "Material slot references a missing material.",
                    code="materials.missing",
                    object_path=path,
                )
                continue
            _validate_material_textures(report, material, path, fbx_module)

    return report


def ValidateAnimation(scene, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("Animation")
    anim_stack_count = scene.GetSrcObjectCount(fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId))
    if anim_stack_count == 0:
        report.add_issue(
            "WARN",
            "Scene has no animation stacks.",
            code="animation.no_stacks",
            object_path="<animation>",
        )
        return report

    for stack_index in range(anim_stack_count):
        stack = scene.GetSrcObject(fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId), stack_index)
        if stack is None:
            continue
        span = stack.GetLocalTimeSpan()
        if span.GetStart().Get() >= span.GetStop().Get():
            report.add_issue(
                "FAIL",
                f"Animation stack '{stack.GetName()}' has invalid time span.",
                code="animation.time_span",
                object_path=f"<animation>/{stack.GetName() or stack_index}",
            )
        layer_count = stack.GetMemberCount()
        if layer_count == 0:
            report.add_issue(
                "WARN",
                f"Animation stack '{stack.GetName()}' has no layers.",
                code="animation.no_layers",
                object_path=f"<animation>/{stack.GetName() or stack_index}",
            )

    return report


def ValidateConstraints(scene, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("Constraints")
    criteria = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxConstraint.ClassId)
    constraint_count = scene.GetSrcObjectCount(criteria)
    for index in range(constraint_count):
        constraint = scene.GetSrcObject(criteria, index)
        if constraint is None:
            continue
        if constraint.GetConstraintSourceCount() == 0 or constraint.GetConstrainedObjectCount() == 0:
            report.add_issue(
                "FAIL",
                f"Constraint '{constraint.GetName()}' is missing sources or targets.",
                code="constraints.links",
                object_path=f"<constraints>/{constraint.GetName() or index}",
            )
    return report


def ValidateConnections(scene, fbx_module) -> ValidationCategoryReport:  # type: ignore[valid-type]
    report = ValidationCategoryReport("Connections")
    root = scene.GetRootNode()
    if root is None:
        return report

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    for node in iter_nodes(root):
        attr = node.GetNodeAttribute()
        if isinstance(attr, fbx_module.FbxMesh):
            if attr.GetDeformerCount(fbx_module.FbxDeformer.eSkin) > 0:
                has_cluster_links = False
                for skin_index in range(attr.GetDeformerCount(fbx_module.FbxDeformer.eSkin)):
                    skin = attr.GetDeformer(skin_index, fbx_module.FbxDeformer.eSkin)
                    if skin is None:
                        continue
                    for cluster_index in range(skin.GetClusterCount()):
                        cluster = skin.GetCluster(cluster_index)
                        if cluster is not None and cluster.GetLink() is not None:
                            has_cluster_links = True
                            break
                    if has_cluster_links:
                        break
                if not has_cluster_links:
                    report.add_issue(
                        "FAIL",
                        "Skinned mesh lacks valid joint connections.",
                        code="connections.mesh_skin_links",
                        object_path=_node_path(node),
                    )
            if node.GetMaterialCount() > 0 and attr.GetElementMaterialCount() == 0:
                report.add_issue(
                    "WARN",
                    "Mesh has materials assigned but no material layer element.",
                    code="connections.material_layer",
                    object_path=_node_path(node),
                )

    return report


# ---------------------------------------------------------------------------
# Auto repair helpers


def AutoRepair(
    report: ValidationReport,
    scene,
    canonical: CanonicalSettings,
    fbx_module,
) -> None:  # type: ignore[valid-type]
    """Attempt to repair known issues in the scene."""

    globals_settings = scene.GetGlobalSettings()

    for issue in list(report.categories.get("globals", ValidationCategoryReport("globals")).issues):
        if issue.code == "globals.axis" and canonical.axis_system is not None:
            globals_settings.SetAxisSystem(canonical.axis_system)
            issue.fix_applied = "Axis system reset to canonical orientation."
            report.repairs.append(
                {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
            )
        elif issue.code == "globals.system_unit" and canonical.system_unit is not None:
            globals_settings.SetSystemUnit(canonical.system_unit)
            issue.fix_applied = "System unit reset to canonical scale."
            report.repairs.append(
                {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
            )
        elif issue.code == "globals.time_mode" and canonical.time_mode is not None:
            try:
                globals_settings.SetTimeMode(canonical.time_mode)
            except TypeError:
                # Some SDKs expect an explicit FbxTime.EMode; attempt to coerce
                coerced_mode = None
                time_class = getattr(fbx_module, "FbxTime", None)
                mode_enum = getattr(time_class, "EMode", None) if time_class else None
                if mode_enum is not None and isinstance(canonical.time_mode, int):
                    try:
                        coerced_mode = mode_enum(canonical.time_mode)
                    except Exception:  # pragma: no cover - defensive fallback
                        coerced_mode = None
                if coerced_mode is not None:
                    globals_settings.SetTimeMode(coerced_mode)
                else:  # pragma: no cover - defensive fallback
                    issue.fix_applied = (
                        "Unable to reset time mode due to incompatible SDK signature."
                    )
                    report.repairs.append(
                        {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
                    )
                    continue

            if canonical.time_mode == getattr(fbx_module.FbxTime, "eCustom", None):
                set_custom_rate = getattr(globals_settings, "SetCustomFrameRate", None)
                if callable(set_custom_rate):
                    set_custom_rate(canonical.frame_rate)
                else:  # pragma: no cover - defensive fallback
                    issue.fix_applied = (
                        "Time mode set, but custom frame-rate setter unavailable on this SDK."
                    )
                    report.repairs.append(
                        {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
                    )
                    continue

            issue.fix_applied = "Time mode reset to canonical mode."
            report.repairs.append(
                {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
            )
        elif issue.code == "globals.frame_rate" and canonical.time_mode == getattr(
            fbx_module.FbxTime, "eCustom", None
        ):
            set_custom_rate = getattr(globals_settings, "SetCustomFrameRate", None)
            if callable(set_custom_rate):
                set_custom_rate(canonical.frame_rate)
                issue.fix_applied = "Custom frame rate synced to canonical value."
            else:  # pragma: no cover - defensive fallback
                issue.fix_applied = "Unable to set custom frame rate; setter unavailable."
            report.repairs.append(
                {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
            )
        elif issue.code == "globals.time_span":
            span = fbx_module.FbxTimeSpan()
            scene.GetGlobalSettings().GetTimelineDefaultTimeSpan(span)
            if canonical.time_span is not None:
                start_time = fbx_module.FbxTime()
                stop_time = fbx_module.FbxTime()
                start_time.Set(canonical.time_span[0])
                stop_time.Set(canonical.time_span[1])
                span.SetStart(start_time)
                span.SetStop(stop_time)
            else:
                # Fallback to minimal frame range if unknown.
                start_time = fbx_module.FbxTime()
                stop_time = fbx_module.FbxTime()
                start_time.Set(0)
                stop_time.Set(int(fbx_module.FbxTime.GetOneFrameValue()))
                span.SetStart(start_time)
                span.SetStop(stop_time)
            globals_settings.SetTimelineDefaultTimeSpan(span)
            issue.fix_applied = "Global time span reset to a valid range."
            report.repairs.append(
                {"object": issue.object_path or "<globals>", "action": issue.fix_applied}
            )

    root = scene.GetRootNode()
    if root is None:
        return

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    # Repair skinning matrices and bind poses if required.
    skin_category = report.categories.get("skin")
    needs_bind_pose = False
    if skin_category is not None:
        for issue in skin_category.issues:
            path = issue.object_path or "<mesh>"
            if issue.code in {"skin.cluster_matrix", "skin.cluster_link_matrix"}:
                node = _find_node_by_path(root, path)
                if node is None:
                    continue
                mesh_attr = node.GetNodeAttribute()
                if not isinstance(mesh_attr, fbx_module.FbxMesh):
                    continue
                mesh_matrix = node.EvaluateGlobalTransform()
                for skin_index in range(mesh_attr.GetDeformerCount(fbx_module.FbxDeformer.eSkin)):
                    skin = mesh_attr.GetDeformer(skin_index, fbx_module.FbxDeformer.eSkin)
                    if skin is None:
                        continue
                    for cluster_index in range(skin.GetClusterCount()):
                        cluster = skin.GetCluster(cluster_index)
                        if cluster is None:
                            continue
                        if issue.code == "skin.cluster_matrix":
                            cluster.SetTransformMatrix(mesh_matrix)
                        link = cluster.GetLink()
                        if link is not None:
                            link_matrix = link.EvaluateGlobalTransform()
                            cluster.SetTransformLinkMatrix(link_matrix)
                issue.fix_applied = "Skin cluster matrices rebuilt from current pose."
                report.repairs.append({"object": path, "action": issue.fix_applied})
            elif issue.code in {"skin.bind_pose_missing", "skin.bind_pose_empty"}:
                needs_bind_pose = True

    if needs_bind_pose:
        pose = fbx_module.FbxPose.Create(scene, "AutoBindPose")
        pose.SetIsBindPose(True)
        for node in iter_nodes(root):
            matrix = node.EvaluateGlobalTransform()
            pose.Add(node, matrix)
        report.repairs.append({"object": "<poses>", "action": "Bind pose reconstructed."})
        if skin_category is not None:
            for issue in skin_category.issues:
                if issue.code in {"skin.bind_pose_missing", "skin.bind_pose_empty"}:
                    issue.fix_applied = "Bind pose reconstructed."


# ---------------------------------------------------------------------------
# Metrics helpers


def collect_scene_metrics(scene, fbx_module, mesh_metrics: Dict[str, MeshMetrics]) -> SceneMetrics:  # type: ignore[valid-type]
    metrics = SceneMetrics()
    root = scene.GetRootNode()
    if root is None:
        return metrics

    def iter_nodes(node):
        yield node
        for idx in range(node.GetChildCount()):
            yield from iter_nodes(node.GetChild(idx))

    nodes = list(iter_nodes(root))
    metrics.node_count = len(nodes)

    metrics.mesh_metrics = dict(mesh_metrics)

    criteria_material = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxSurfaceMaterial.ClassId)
    metrics.material_count = scene.GetSrcObjectCount(criteria_material)

    criteria_texture = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxTexture.ClassId)
    metrics.texture_count = scene.GetSrcObjectCount(criteria_texture)

    metrics.skin_cluster_count = 0
    for node in nodes:
        attr = node.GetNodeAttribute()
        if isinstance(attr, fbx_module.FbxMesh):
            for skin_index in range(attr.GetDeformerCount(fbx_module.FbxDeformer.eSkin)):
                skin = attr.GetDeformer(skin_index, fbx_module.FbxDeformer.eSkin)
                if skin is not None:
                    metrics.skin_cluster_count += skin.GetClusterCount()

    metrics.bind_pose_count = sum(1 for idx in range(scene.GetPoseCount()) if scene.GetPose(idx).IsBindPose())

    criteria_anim_stack = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId)
    metrics.anim_stack_count = scene.GetSrcObjectCount(criteria_anim_stack)

    criteria_anim_curve = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimCurve.ClassId)
    metrics.anim_curve_count = scene.GetSrcObjectCount(criteria_anim_curve)

    return metrics


# ---------------------------------------------------------------------------
# Round-trip validation


def round_trip_check(
    export_path: str,
    *,
    canonical_settings: Optional[CanonicalSettings] = None,
    baseline_metrics: Optional[SceneMetrics] = None,
) -> RoundTripDiffReport:
    manager = sdk.create_manager()
    try:
        sdk.create_io_settings(manager)
        scene = sdk.create_scene(manager)
        if not sdk.load_scene(manager, scene, export_path):
            raise RuntimeError(f"Failed to reload exported FBX '{export_path}' for validation")

        validator = SceneValidator(scene, canonical_settings=canonical_settings)
        validation_report = validator.validate()
        metrics_diff = []
        if baseline_metrics is not None:
            metrics_diff = validation_report.metrics.diff(baseline_metrics)
        return RoundTripDiffReport(validation=validation_report, metrics_diff=metrics_diff)
    finally:
        sdk.destroy_manager(manager)


# ---------------------------------------------------------------------------
# Internal helpers


def _validate_layer_element(
    report: ValidationCategoryReport, element, label: str, object_path: str, fbx_module
) -> None:  # type: ignore[valid-type]
    mapping_mode = element.GetMappingMode()
    reference_mode = element.GetReferenceMode()
    direct = element.GetDirectArray() if hasattr(element, "GetDirectArray") else None
    index = element.GetIndexArray() if hasattr(element, "GetIndexArray") else None

    if direct is not None and direct.GetCount() == 0:
        report.add_issue(
            "WARN",
            f"Layer element {label} has no direct data.",
            code=f"geometry.layer.{label}.empty",
            object_path=object_path,
        )
    mapping_none = getattr(fbx_module.FbxLayerElement, "eNone", None)
    if mapping_none is not None and mapping_mode == mapping_none:  # pragma: no cover - defensive
        report.add_issue(
            "FAIL",
            f"Layer element {label} has invalid mapping mode.",
            code=f"geometry.layer.{label}.mapping",
            object_path=object_path,
        )
    reference_direct = getattr(fbx_module.FbxLayerElement, "eDirect", None)
    if (
        index is not None
        and index.GetCount() == 0
        and reference_direct is not None
        and reference_mode != reference_direct
    ):
        report.add_issue(
            "WARN",
            f"Layer element {label} has empty index array.",
            code=f"geometry.layer.{label}.index",
            object_path=object_path,
        )


def _validate_material_textures(report: ValidationCategoryReport, material, node_path: str, fbx_module) -> None:  # type: ignore[valid-type]
    texture_channel_names = [
        "Diffuse",
        "Specular",
        "NormalMap",
        "Bump",
        "Emissive",
        "BaseColor",
    ]
    for channel in texture_channel_names:
        property_handle = material.FindProperty(fbx_module.FbxSurfaceMaterial.sDiffuse if channel == "Diffuse" else channel)
        if not property_handle.IsValid():
            continue
        texture_count = property_handle.GetSrcObjectCount(fbx_module.FbxTexture.ClassId)
        if texture_count == 0:
            continue
        for texture_index in range(texture_count):
            texture = property_handle.GetSrcObject(fbx_module.FbxTexture.ClassId, texture_index)
            if texture is None:
                report.add_issue(
                    "WARN",
                    f"Texture slot '{channel}' is missing its texture connection.",
                    code="materials.texture_missing",
                    object_path=node_path,
                )


def _node_path(node) -> str:  # type: ignore[valid-type]
    names: List[str] = []
    current = node
    while current is not None:
        names.append(current.GetName() or "<unnamed>")
        current = current.GetParent()
    return "/" + "/".join(reversed(names))


def _find_node_by_path(root, path: str):  # type: ignore[valid-type]
    target = path.split("/")
    target = [segment for segment in target if segment]
    if not target:
        return root

    def match(node, segments):
        if not segments:
            return node
        name = segments[0]
        if (node.GetName() or "") != name:
            return None
        if len(segments) == 1:
            return node
        for idx in range(node.GetChildCount()):
            child = node.GetChild(idx)
            result = match(child, segments[1:])
            if result is not None:
                return result
        return None

    for idx in range(root.GetChildCount()):
        child = root.GetChild(idx)
        result = match(child, target)
        if result is not None:
            return result
    return None

