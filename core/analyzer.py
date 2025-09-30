"""High level analyzer orchestration."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Protocol

from .exceptions import FBXLoadError
from . import sdk


class SceneInspector(Protocol):
    """Protocol defining how inspectors gather data from an FBX scene."""

    id: str

    def collect(self, context: "SceneContext") -> Any:
        """Return extracted information from the scene."""


@dataclass
class SceneContext:
    """Holds the FBX manager, scene, and convenience handles."""

    path: str
    manager: Any
    scene: Any
    root_node: Any


class FBXAnalyzer(contextlib.AbstractContextManager["FBXAnalyzer"]):
    """Loads an FBX file and coordinates data extraction."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._manager: Optional[Any] = None
        self._scene: Optional[Any] = None

    @property
    def path(self) -> str:
        return self._path

    @property
    def context(self) -> SceneContext:
        if self._manager is None or self._scene is None:
            raise RuntimeError("Analyzer not loaded. Call load() before accessing context.")
        fbx, _ = sdk.import_fbx_module()
        root = self._scene.GetRootNode()
        return SceneContext(
            path=self._path,
            manager=self._manager,
            scene=self._scene,
            root_node=root,
        )

    def load(self) -> "FBXAnalyzer":
        if self._manager is not None:
            return self

        manager = sdk.create_manager()
        sdk.create_io_settings(manager)
        scene = sdk.create_scene(manager)
        if not sdk.load_scene(manager, scene, self._path):
            sdk.destroy_manager(manager)
            raise FBXLoadError(f"Failed to load FBX scene from '{self._path}'")

        self._manager = manager
        self._scene = scene
        return self

    def close(self) -> None:
        if self._manager is not None:
            sdk.destroy_manager(self._manager)
            self._manager = None
            self._scene = None

    def __exit__(self, exc_type, exc, tb) -> Optional[bool]:
        self.close()
        return None

    # Allow usage as context manager via `with FBXAnalyzer(path) as analyzer:`
    def __enter__(self) -> "FBXAnalyzer":
        return self.load()

    def run(self, inspectors: Iterable[SceneInspector]) -> Dict[str, Any]:
        """Execute inspectors and return their aggregated results."""

        results: Dict[str, Any] = {}
        ctx = self.context
        for inspector in inspectors:
            results[inspector.id] = inspector.collect(ctx)
        return results
