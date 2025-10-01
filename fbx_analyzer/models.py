"""Domain models used across the analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

Vector3 = Tuple[float, float, float]


@dataclass
class JointTransform:
    translation: Vector3
    rotation: Vector3
    scaling: Vector3


@dataclass
class Joint:
    name: str
    joint_type: str
    transform: JointTransform
    children: List["Joint"] = field(default_factory=list)
    parent_name: Optional[str] = None
    properties: Dict[str, str] = field(default_factory=dict)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Skeleton:
    name: str
    root: Joint

    @property
    def joint_count(self) -> int:
        return sum(1 for _ in self.root.walk())


@dataclass
class SceneNode:
    name: str
    attribute_type: str
    attribute_class: str
    translation: Vector3
    rotation: Vector3
    scaling: Vector3
    child_count: int
    uid: Optional[int] = None
    parent_uid: Optional[int] = None
    original_path: Tuple[int, ...] = ()
    properties: Dict[str, str] = field(default_factory=dict)
    children: List['SceneNode'] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

@dataclass
class AnalyzedScene:
    path: str
    skeletons: List[Skeleton]
    scene_graph: Optional[SceneNode]
    top_level_nodes: List[Dict[str, Any]]
