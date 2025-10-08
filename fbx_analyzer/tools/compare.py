from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core import sdk
from ..core.analyzer import FBXAnalyzer
from ..inspectors.skeleton import SkeletonInspector


@dataclass
class FileSummary:
    path: str
    axis_up: Optional[str] = None
    system_unit_scale: Optional[float] = None
    skeleton_roots: List[str] = None
    skeleton_joint_names: List[str] = None
    anim_stack_count: int = 0
    anim_layer_count: int = 0
    animated_node_names: List[str] = None


@dataclass
class Comparison:
    base_skeleton: str
    candidate: str
    checked_root: Optional[str]
    has_root_track: Optional[bool]
    missing_joints_in_candidate: List[str]
    extra_animated_in_candidate: List[str]


def _axis_up_to_str(axis_system) -> Optional[str]:
    try:
        up_vec = axis_system.GetUpVector()
        axis = int(up_vec[0])
        sign = int(up_vec[1])
        name = {0: "X", 1: "Y", 2: "Z"}.get(axis, str(axis))
        return f"{name}{'+' if sign > 0 else '-'}"
    except Exception:
        return None


def _collect_anim_layers(scene, fbx_module) -> List[Any]:
    layers: List[Any] = []
    try:
        criteria = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimLayer.ClassId)
        count = scene.GetSrcObjectCount(criteria)
        for idx in range(count or 0):
            lyr = scene.GetSrcObject(criteria, idx)
            if lyr:
                layers.append(lyr)
    except Exception:
        pass

    if layers:
        return layers

    # Fallback via stacks
    try:
        scrit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId)
        scount = scene.GetSrcObjectCount(scrit)
        for si in range(scount or 0):
            stack = scene.GetSrcObject(scrit, si)
            if not stack:
                continue
            try:
                lc = stack.GetMemberCount()
            except Exception:
                lc = 0
            for li in range(lc or 0):
                lyr = stack.GetMember(li)
                if lyr:
                    layers.append(lyr)
    except Exception:
        pass
    return layers


def _node_has_any_curve(node, layers: List[Any]) -> bool:
    props = (node.LclTranslation, node.LclRotation, node.LclScaling)
    for layer in layers:
        for prop in props:
            try:
                cn = prop.GetCurveNode(layer)
            except Exception:
                cn = None
            if cn:
                return True
    return False


def _collect_animated_node_names(scene, fbx_module) -> Tuple[int, int, Set[str]]:
    names: Set[str] = set()
    layers = _collect_anim_layers(scene, fbx_module)
    anim_layer_count = len(layers)
    anim_stack_crit = fbx_module.FbxCriteria.ObjectType(fbx_module.FbxAnimStack.ClassId)
    try:
        anim_stack_count = int(scene.GetSrcObjectCount(anim_stack_crit))
    except Exception:
        anim_stack_count = 0

    def walk(n):
        if _node_has_any_curve(n, layers):
            names.add(n.GetName() or "")
        for i in range(n.GetChildCount()):
            walk(n.GetChild(i))

    root = scene.GetRootNode()
    if root:
        walk(root)
    return anim_stack_count, anim_layer_count, names


def summarize_file(path: Path) -> FileSummary:
    fbx, _ = sdk.import_fbx_module()
    mgr = sdk.create_manager()
    try:
        sdk.create_io_settings(mgr)
        sc = sdk.create_scene(mgr)
        if not sdk.load_scene(mgr, sc, str(path)):
            raise RuntimeError(f"Failed to load {path}")

        gs = sc.GetGlobalSettings()
        axis_up = _axis_up_to_str(gs.GetAxisSystem())
        try:
            scale = float(gs.GetSystemUnit().GetScaleFactor())
        except Exception:
            scale = None

        skel_ins = SkeletonInspector()
        with FBXAnalyzer(str(path)) as az:
            res = az.run([skel_ins])
        skeletons = res.get(skel_ins.id, []) or []
        roots = [s.root.name for s in skeletons]
        joint_names: Set[str] = set()
        for s in skeletons:
            stack = [s.root]
            while stack:
                j = stack.pop()
                joint_names.add(j.name)
                stack.extend(j.children)

        a_count, l_count, animated = _collect_animated_node_names(sc, fbx)

        return FileSummary(
            path=str(path),
            axis_up=axis_up,
            system_unit_scale=scale,
            skeleton_roots=roots,
            skeleton_joint_names=sorted(joint_names),
            anim_stack_count=a_count,
            anim_layer_count=l_count,
            animated_node_names=sorted(animated),
        )
    finally:
        sdk.destroy_manager(mgr)


def compare(base: FileSummary, candidate: FileSummary) -> List[Comparison]:
    results: List[Comparison] = []
    if not base.skeleton_roots:
        return results

    candidate_animated = set(candidate.animated_node_names or [])
    for root in base.skeleton_roots:
        has_track = root in candidate_animated if candidate.anim_layer_count > 0 else False
        missing = []
        if base.skeleton_joint_names:
            missing = [n for n in base.skeleton_joint_names if n and n not in candidate_animated]
        results.append(
            Comparison(
                base_skeleton=base.path,
                candidate=candidate.path,
                checked_root=root,
                has_root_track=has_track,
                missing_joints_in_candidate=missing,
                extra_animated_in_candidate=[n for n in candidate_animated if n not in (base.skeleton_joint_names or [])],
            )
        )
    return results


def main(argv: List[str]) -> int:
    if not argv:
        print("Usage: python -m fbx_analyzer.tools.compare <base_skeleton.fbx> <candidate1.fbx> [candidate2.fbx ...]")
        return 2

    base = Path(argv[0])
    candidates = [Path(p) for p in argv[1:]]
    if not base.exists():
        print(f"Base file not found: {base}")
        return 2
    for p in candidates:
        if not p.exists():
            print(f"Candidate not found: {p}")
            return 2

    base_sum = summarize_file(base)
    cand_summaries = [summarize_file(p) for p in candidates]

    comparisons: List[Comparison] = []
    for c in cand_summaries:
        comparisons.extend(compare(base_sum, c))

    # Write report to logs
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = logs_dir / f"compare_{ts}.json"
    payload: Dict[str, Any] = {
        "base": asdict(base_sum),
        "candidates": [asdict(c) for c in cand_summaries],
        "comparisons": [asdict(r) for r in comparisons],
        "hints": [
            "If has_root_track is false, add an identity root track to the animation.",
            "Align axis system and units across files (e.g., MayaYUp, centimeters).",
            "Prefer patching the animation rather than removing the skeleton root.",
        ],
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

