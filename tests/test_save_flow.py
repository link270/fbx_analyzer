from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pytest

from fbx_analyzer.core import FBXAnalyzer
from fbx_analyzer.core.exceptions import FBXLoadError, FBXSDKNotAvailableError, FBXSaveError
from fbx_analyzer.core.save_as import rebuild_scene_graph_as, save_scene_graph_as, SceneExportDiagnostics
from fbx_analyzer.core.validation import SceneValidator
from fbx_analyzer.inspectors.scene_graph import SceneGraphInspector


def _has_fbx_sdk() -> bool:
    try:
        import fbx  # type: ignore
        import FbxCommon  # type: ignore
        return True
    except Exception:
        return False


sdk_available = pytest.mark.skipif(not _has_fbx_sdk(), reason="FBX SDK not available")


def _first_scene_graph(document) -> Optional["SceneNode"]:
    return document.scene_graph


def _find_node_by_name(root, name: str):
    if root is None:
        return None
    if root.name == name:
        return root
    for child in root.children:
        found = _find_node_by_name(child, name)
        if found is not None:
            return found
    return None


def _find_leaf(root):
    if root is None:
        return None
    if not root.children:
        return root
    for child in root.children:
        leaf = _find_leaf(child)
        if leaf is not None:
            return leaf
    return None


def _reload_and_validate(path: str):
    from fbx_analyzer.core import sdk
    manager = sdk.create_manager()
    try:
        sdk.create_io_settings(manager)
        scene = sdk.create_scene(manager)
        assert sdk.load_scene(manager, scene, path), f"Failed to reload saved scene: {path}"
        validator = SceneValidator(scene)
        report = validator.validate()
        assert report.export_ready, f"Reloaded scene has validation failures: {report.status_summary()}"
        return report
    finally:
        sdk.destroy_manager(manager)


@sdk_available
def test_import_animation_analyze(data_dir: Path):
    src = data_dir / "example_animation.fbx"
    assert src.exists(), "Missing example_animation.fbx"
    with FBXAnalyzer(str(src)) as analyzer:
        results = analyzer.run([SceneGraphInspector()])
        assert "scene_graph" in results


@sdk_available
def test_animation_edit_save_roundtrip(data_dir: Path, tmp_outputs: Path, keep_outputs_flag: bool):
    src = data_dir / "example_animation.fbx"
    dst = tmp_outputs / "example_animation_edit.fbx"
    assert src.exists()

    # Analyze to get a mutable scene graph
    with FBXAnalyzer(str(src)) as analyzer:
        results = analyzer.run([SceneGraphInspector()])
        scene_graph = results.get("scene_graph")
        assert scene_graph is not None, "No scene graph produced for animation file"

        # Edit 1: change a node type where supported by saver
        # Prefer skeleton nodes which the saver knows how to map
        target = _find_node_by_name(scene_graph, "Hips") or scene_graph
        original_type = target.attribute_type
        target.attribute_type = "Root" if original_type != "Root" else "LimbNode"

        diags = SceneExportDiagnostics()
        rebuild_scene_graph_as(str(src), str(dst), scene_graph, diagnostics=diags)
        _reload_and_validate(str(dst))

        if not keep_outputs_flag:
            try:
                os.remove(dst)
            except OSError:
                pass


@sdk_available
def test_animation_delete_and_reparent_save(data_dir: Path, tmp_outputs: Path, keep_outputs_flag: bool):
    src = data_dir / "example_animation.fbx"
    dst1 = tmp_outputs / "example_animation_delete.fbx"
    dst2 = tmp_outputs / "example_animation_reparent.fbx"

    with FBXAnalyzer(str(src)) as analyzer:
        results = analyzer.run([SceneGraphInspector()])
        scene_graph = results.get("scene_graph")
        assert scene_graph is not None

        # Delete a leaf node to minimize disruption
        leaf = _find_leaf(scene_graph)
        if leaf is not None and leaf is not scene_graph:
            # Remove leaf from its parent
            def _remove(current, target):
                if current is None:
                    return False
                for child in list(current.children):
                    if child is target:
                        current.children.remove(child)
                        return True
                    if _remove(child, target):
                        return True
                return False

            assert _remove(scene_graph, leaf)

        diags = SceneExportDiagnostics()
        rebuild_scene_graph_as(str(src), str(dst1), scene_graph, diagnostics=diags)
        _reload_and_validate(str(dst1))

        # Reparent: move first child (if present) under root to test reparenting path
        if scene_graph.children:
            child = scene_graph.children[0]
            # If already direct child of root, try moving it under itself won't work; pick sibling if possible
            if len(scene_graph.children) > 1:
                child2 = scene_graph.children[1]
                scene_graph.children.remove(child2)
                child.children.append(child2)
            else:
                # Move leaf under child
                node_to_move = _find_leaf(scene_graph)
                if node_to_move is not None and node_to_move is not child:
                    # Detach from parent
                    def _detach(cur, target):
                        for ch in list(cur.children):
                            if ch is target:
                                cur.children.remove(ch)
                                return True
                            if _detach(ch, target):
                                return True
                        return False
                    _detach(scene_graph, node_to_move)
                    child.children.append(node_to_move)

        diags2 = SceneExportDiagnostics()
        rebuild_scene_graph_as(str(src), str(dst2), scene_graph, diagnostics=diags2)
        _reload_and_validate(str(dst2))

        if not keep_outputs_flag:
            for p in (dst1, dst2):
                try:
                    os.remove(p)
                except OSError:
                    pass


@sdk_available
def test_character_basic_roundtrip(data_dir: Path, tmp_outputs: Path, keep_outputs_flag: bool):
    src = data_dir / "example_character.fbx"
    dst = tmp_outputs / "example_character_roundtrip.fbx"
    assert src.exists()

    with FBXAnalyzer(str(src)) as analyzer:
        results = analyzer.run([SceneGraphInspector()])
        scene_graph = results.get("scene_graph")
        assert scene_graph is not None

        # Minimal transform tweak on root to exercise transform writes without disrupting skin
        tx, ty, tz = scene_graph.translation
        scene_graph.translation = (tx, ty, tz)

        diags = SceneExportDiagnostics()
        rebuild_scene_graph_as(str(src), str(dst), scene_graph, diagnostics=diags)
        _reload_and_validate(str(dst))

        if not keep_outputs_flag:
            try:
                os.remove(dst)
            except OSError:
                pass


def test_verbatim_copy_when_no_edits(data_dir: Path, tmp_outputs: Path, keep_outputs_flag: bool):
    src = data_dir / "example_character.fbx"
    dst = tmp_outputs / "example_character_copy.fbx"
    assert src.exists()

    # When scene_graph is None, save_scene_graph_as performs a verbatim copy
    diags = save_scene_graph_as(str(src), str(dst), scene_graph=None, diagnostics=SceneExportDiagnostics())
    assert diags is not None
    assert dst.exists(), "Expected verbatim copy to be created"

    # Reload to ensure the file is readable (if SDK available).
    # For verbatim copy we do not enforce export_ready, since the source may
    # intentionally contain validation warnings/failures.
    if _has_fbx_sdk():
        from fbx_analyzer.core import sdk
        mgr = sdk.create_manager()
        try:
            sdk.create_io_settings(mgr)
            scene = sdk.create_scene(mgr)
            assert sdk.load_scene(mgr, scene, str(dst))
        finally:
            sdk.destroy_manager(mgr)

    if not keep_outputs_flag:
        try:
            os.remove(dst)
        except OSError:
            pass
