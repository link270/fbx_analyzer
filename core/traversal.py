"""Utilities for traversing FBX scene graphs."""

from __future__ import annotations

from typing import Iterable, Iterator


def iter_nodes(root) -> Iterator:
    """Yield nodes depth-first starting at `root` (inclusive)."""

    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for idx in range(node.GetChildCount() - 1, -1, -1):
            stack.append(node.GetChild(idx))


def iter_by_attribute_type(root, attribute_type) -> Iterator:
    """Yield nodes whose primary attribute matches `attribute_type`."""

    for node in iter_nodes(root):
        attr = node.GetNodeAttribute()
        if attr and attr.GetAttributeType() == attribute_type:
            yield node
