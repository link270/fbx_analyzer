"""Command-line interface for fbx_analyzer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .core import FBXAnalyzer
from .core.exceptions import FBXLoadError, FBXSDKNotAvailableError
from .gui import ask_for_fbx_file, launch_skeleton_viewer
from .inspectors import SceneGraphInspector, SkeletonInspector, TopLevelInspector
from .models import AnalyzedScene


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze FBX files with a focus on skeleton data.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Path to the FBX file to analyze. If omitted, a file picker appears.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Do not launch the GUI; print a text summary instead.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.path is None:
        if args.no_gui:
            parser.error("--no-gui requires that you supply a file path.")
        selected = ask_for_fbx_file()
        if not selected:
            print("No FBX file selected; exiting.")
            return 1
        path = Path(selected)
    else:
        path = args.path

    if not path.exists():
        parser.error(f"File not found: {path}")

    skeleton_inspector = SkeletonInspector()
    top_level_inspector = TopLevelInspector()
    scene_graph_inspector = SceneGraphInspector()

    try:
        with FBXAnalyzer(str(path)) as analyzer:
            results = analyzer.run([skeleton_inspector, top_level_inspector, scene_graph_inspector])
    except FBXSDKNotAvailableError as exc:
        parser.error(str(exc))
    except FBXLoadError as exc:
        parser.error(str(exc))

    skeletons = results.get(skeleton_inspector.id, [])
    top_level_nodes = results.get(top_level_inspector.id, [])
    scene_graph = results.get(scene_graph_inspector.id)

    document = AnalyzedScene(
        path=str(path),
        skeletons=skeletons,
        scene_graph=scene_graph,
        top_level_nodes=top_level_nodes or [],
    )

    _print_top_level_summary(document.top_level_nodes, title=Path(document.path).name)

    if args.no_gui:
        if not document.skeletons:
            print("No skeletons found in file after checking animation data. Ensure the FBX includes rig nodes or skin clusters.")
            return 0

        for idx, skeleton in enumerate(document.skeletons, start=1):
            print(f"Skeleton {idx}: {skeleton.name} ({skeleton.joint_count} joints)")
            for joint in skeleton.root.walk():
                translation = ", ".join(f"{value:.3f}" for value in joint.transform.translation)
                print(f"  - {joint.name} [{joint.joint_type}] (T: {translation})")
        return 0

    launch_skeleton_viewer([document])
    return 0


def _print_top_level_summary(entries: Iterable[Dict[str, Any]], *, title: str) -> None:
    entries = list(entries or [])
    if not entries:
        print(f'Top-level scene nodes ({title}): <none>')
        return

    print(f'Top-level scene nodes ({title}):')
    for entry in entries:
        name = entry.get('name', '<unnamed>')
        attr_type = entry.get('attribute_type', 'Unknown')
        child_count = entry.get('child_count', 0)
        extras = []
        if entry.get('is_mesh'):
            extras.append('mesh')
        attr_class = entry.get('attribute_class')
        if attr_class and attr_class != attr_type:
            extras.append(attr_class)
        extra_str = f" ({', '.join(extras)})" if extras else ''
        print(f"  - {name} [type: {attr_type}, children: {child_count}]{extra_str}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
