"""Autodesk FBX SDK helpers."""

from __future__ import annotations

from typing import Optional

from .exceptions import FBXSDKNotAvailableError


def import_fbx_module():
    """Import the Autodesk FBX SDK Python module.

    Encapsulates the import so code can provide a helpful error when it is
    missing instead of failing at module import time.
    """

    try:
        import fbx  # type: ignore
        import FbxCommon  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependent on external SDK
        raise FBXSDKNotAvailableError(
            "Autodesk FBX SDK Python bindings are not available. "
            "Install the SDK and ensure the 'fbx' and 'FbxCommon' modules are on PYTHONPATH."
        ) from exc

    return fbx, FbxCommon


def create_manager():
    """Create and return an `fbx.FbxManager` instance."""

    fbx, _ = import_fbx_module()
    manager = fbx.FbxManager.Create()
    return manager


def create_io_settings(manager):
    """Create default IO settings for the provided manager."""

    fbx, _ = import_fbx_module()
    ios = fbx.FbxIOSettings.Create(manager, fbx.IOSROOT)
    manager.SetIOSettings(ios)
    return ios


def load_scene(manager, scene, path: str) -> bool:
    """Load an FBX file located at `path` into `scene`."""

    fbx, FbxCommon = import_fbx_module()
    importer = fbx.FbxImporter.Create(manager, "")
    try:
        if not importer.Initialize(path, -1, manager.GetIOSettings()):
            return False
        if not importer.Import(scene):
            return False
        return True
    finally:
        importer.Destroy()


def create_scene(manager):
    """Create a new scene using the provided manager."""

    fbx, _ = import_fbx_module()
    return fbx.FbxScene.Create(manager, "Scene")


def destroy_manager(manager):
    """Destroy the manager and free SDK resources."""

    manager.Destroy()


def save_scene(manager, scene, path: str) -> bool:
    """Save the provided FBX scene to ``path``."""

    fbx, _ = import_fbx_module()
    exporter = fbx.FbxExporter.Create(manager, "")
    try:
        registry = manager.GetIOPluginRegistry()
        file_format = registry.GetNativeWriterFormat()
        if not exporter.Initialize(path, file_format, manager.GetIOSettings()):
            return False
        if not exporter.Export(scene):
            return False
        return True
    finally:
        exporter.Destroy()
