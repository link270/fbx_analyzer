"""Tkinter GUI for presenting FBX information."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

from ..core import FBXAnalyzer
from ..core.exceptions import FBXLoadError, FBXSDKNotAvailableError, FBXSaveError
from ..core.save_as import (
    SceneExportDiagnostics,
    rebuild_scene_graph_as,
    save_scene_graph_as,
)
from ..inspectors import SceneGraphInspector, SceneMetadataInspector, SkeletonInspector, TopLevelInspector
from ..models import (
    AnalyzedScene,
    DefinitionSummary,
    FBXConnectionInfo,
    FBXPropertyEntry,
    Joint,
    SceneMetadata,
    SceneNode,
    SceneObjectInfo,
    Skeleton,
)


DEFAULT_ATTRIBUTE_OPTIONS: Tuple[str, ...] = (
    "Node",
    "Null",
    "Skeleton",
    "Root",
    "Limb",
    "LimbNode",
    "Effector",
)


class DocumentPane:
    """Render a single FBX analysis inside a notebook tab."""

    def __init__(self, parent: ttk.Notebook, document: AnalyzedScene) -> None:
        self.document = document
        self.frame = ttk.Frame(parent)

        self._display_name_var = tk.StringVar(value="")
        self._full_path_var = tk.StringVar(value="")

        self._joint_map: Dict[str, Joint] = {}
        self._node_map: Dict[str, SceneNode] = {}
        self._reparent_target: Optional[SceneNode] = None
        self._attribute_options = list(DEFAULT_ATTRIBUTE_OPTIONS)

        self.metadata: SceneMetadata = document.metadata or SceneMetadata()
        self._object_metadata: Dict[int, SceneObjectInfo] = {obj.uid: obj for obj in self.metadata.objects}

        self.update_document_path(self.document.path)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction

    def _build_ui(self) -> None:
        header = ttk.Frame(self.frame, padding=(10, 6))
        header.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(
            header,
            textvariable=self._display_name_var,
            font=("Helvetica", 12, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Label(
            header,
            textvariable=self._full_path_var,
            font=("Helvetica", 9),
            foreground="#666666",
            wraplength=600,
        ).pack(side=tk.LEFT, padx=(10, 0))

        notebook = ttk.Notebook(self.frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        scene_frame = ttk.Frame(notebook)
        notebook.add(scene_frame, text="Scene Nodes")
        self._build_scene_tab(scene_frame)

        skeleton_frame = ttk.Frame(notebook)
        notebook.add(skeleton_frame, text="Skeleton")
        self._build_skeleton_tab(skeleton_frame)

        metadata_frame = ttk.Frame(notebook)
        notebook.add(metadata_frame, text="Metadata")
        self._build_metadata_tab(metadata_frame)

    # ------------------------------------------------------------------
    # Skeleton tab

    def _build_skeleton_tab(self, container: ttk.Frame) -> None:
        skeletons = self.document.skeletons
        if not skeletons:
            ttk.Label(
                container,
                text=(
                    "No skeletons were detected. Use the Scene Nodes tab to inspect the raw hierarchy."
                ),
                padding=20,
                wraplength=600,
                justify=tk.LEFT,
            ).pack(fill=tk.BOTH, expand=True)
            return

        main_pane = ttk.Panedwindow(container, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(main_pane, padding=8)
        main_pane.add(tree_frame, weight=1)

        detail_container = ttk.Frame(main_pane)
        main_pane.add(detail_container, weight=2)

        detail_canvas = tk.Canvas(detail_container, highlightthickness=0)
        detail_scrollbar = ttk.Scrollbar(detail_container, orient=tk.VERTICAL, command=detail_canvas.yview)
        detail_canvas.configure(yscrollcommand=detail_scrollbar.set)
        detail_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail_frame = ttk.Frame(detail_canvas, padding=8)
        detail_window = detail_canvas.create_window((0, 0), window=detail_frame, anchor="nw")

        def _configure_detail_frame(_event) -> None:
            detail_canvas.configure(scrollregion=detail_canvas.bbox("all"))
            detail_canvas.itemconfigure(detail_window, width=detail_canvas.winfo_width())

        detail_frame.bind("<Configure>", _configure_detail_frame)

        def _on_detail_mousewheel(event) -> None:
            delta = getattr(event, "delta", 0)
            if delta:
                detail_canvas.yview_scroll(int(-delta / 120), "units")
            else:
                num = getattr(event, "num", None)
                if num == 5:
                    detail_canvas.yview_scroll(1, "units")
                elif num == 4:
                    detail_canvas.yview_scroll(-1, "units")

        detail_canvas.bind("<MouseWheel>", _on_detail_mousewheel)
        detail_canvas.bind("<Button-4>", _on_detail_mousewheel)
        detail_canvas.bind("<Button-5>", _on_detail_mousewheel)
        detail_canvas.bind("<Configure>", lambda event: detail_canvas.itemconfigure(detail_window, width=event.width))


        self.joint_tree = ttk.Treeview(tree_frame, columns=("type",), show="tree headings")
        self.joint_tree.heading("type", text="Type")
        self.joint_tree.column("type", width=150, anchor=tk.W)
        self.joint_tree.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.joint_tree.yview)
        self.joint_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        detail_title = ttk.Label(detail_frame, text="Joint Details", font=("Helvetica", 14, "bold"))
        detail_title.pack(anchor=tk.W)

        self.joint_detail_vars: Dict[str, tk.StringVar] = {
            "name": tk.StringVar(value=""),
            "type": tk.StringVar(value=""),
            "parent": tk.StringVar(value=""),
            "translation": tk.StringVar(value=""),
            "rotation": tk.StringVar(value=""),
            "scaling": tk.StringVar(value=""),
        }

        detail_rows = ttk.Frame(detail_frame)
        detail_rows.pack(fill=tk.X, expand=False, pady=(8, 0))

        for idx, (label, var) in enumerate(self.joint_detail_vars.items()):
            row = ttk.Frame(detail_rows)
            row.grid(row=idx, column=0, sticky=tk.W, pady=3)
            ttk.Label(row, text=f"{label.title()}:").pack(side=tk.LEFT)
            ttk.Label(row, textvariable=var, font=("Helvetica", 10, "bold"), wraplength=420).pack(
                side=tk.LEFT, padx=4
            )

        self.joint_tree.bind("<<TreeviewSelect>>", self._on_joint_select)
        self._populate_joint_tree(skeletons)

    def _populate_joint_tree(self, skeletons: List[Skeleton]) -> None:
        first_item: Optional[str] = None
        for skeleton in skeletons:
            root_id = self.joint_tree.insert("", tk.END, text=skeleton.name, values=("Skeleton",))
            if first_item is None:
                first_item = root_id
            self._joint_map[root_id] = skeleton.root
            self.joint_tree.item(root_id, open=True)
            self._insert_joint(root_id, skeleton.root)

        if first_item:
            self.joint_tree.selection_set(first_item)
            self._on_joint_select(None)

    def _insert_joint(self, parent_id: str, joint: Joint) -> None:
        node_id = self.joint_tree.insert(parent_id, tk.END, text=joint.name, values=(joint.joint_type,))
        self._joint_map[node_id] = joint
        for child in joint.children:
            self._insert_joint(node_id, child)

    def _on_joint_select(self, _event) -> None:
        if not hasattr(self, "joint_tree"):
            return
        selection = self.joint_tree.selection()
        if not selection:
            return
        joint = self._joint_map.get(selection[0])
        if joint is None:
            return

        self.joint_detail_vars["name"].set(joint.name)
        self.joint_detail_vars["type"].set(joint.joint_type)
        self.joint_detail_vars["parent"].set(joint.parent_name or "-")
        self.joint_detail_vars["translation"].set(_vector_to_string(joint.transform.translation))
        self.joint_detail_vars["rotation"].set(_vector_to_string(joint.transform.rotation))
        self.joint_detail_vars["scaling"].set(_vector_to_string(joint.transform.scaling))

    # ------------------------------------------------------------------
    # Scene nodes tab

    def _build_scene_tab(self, container: ttk.Frame) -> None:
        scene_graph = self.document.scene_graph
        if scene_graph is None:
            ttk.Label(
                container,
                text="Scene graph data could not be constructed for this file.",
                padding=20,
            ).pack(fill=tk.BOTH, expand=True)
            return

        default_attribute = self._attribute_options[0] if self._attribute_options else ""
        self._attribute_choice = tk.StringVar(value=default_attribute)
        self._reparent_target_var = tk.StringVar(value="<none>")
        self._node_status_var = tk.StringVar(value="")
        default_new_type = "LimbNode" if "LimbNode" in self._attribute_options else default_attribute
        self._new_node_name = tk.StringVar(value="")
        self._new_node_attribute = tk.StringVar(value=default_new_type)
        self._node_name_edit = tk.StringVar(value="")
        self._translation_edit = tk.StringVar(value="")
        self._rotation_edit = tk.StringVar(value="")
        self._scaling_edit = tk.StringVar(value="")

        main_pane = ttk.Panedwindow(container, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(main_pane, padding=8)
        main_pane.add(tree_frame, weight=1)

        detail_container = ttk.Frame(main_pane)
        main_pane.add(detail_container, weight=2)

        detail_canvas = tk.Canvas(detail_container, highlightthickness=0)
        detail_scrollbar = ttk.Scrollbar(detail_container, orient=tk.VERTICAL, command=detail_canvas.yview)
        detail_canvas.configure(yscrollcommand=detail_scrollbar.set)
        detail_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail_frame = ttk.Frame(detail_canvas, padding=8)
        detail_window = detail_canvas.create_window((0, 0), window=detail_frame, anchor="nw")

        def _configure_detail_frame(_event) -> None:
            detail_canvas.configure(scrollregion=detail_canvas.bbox("all"))
            detail_canvas.itemconfigure(detail_window, width=detail_canvas.winfo_width())

        detail_frame.bind("<Configure>", _configure_detail_frame)

        def _on_detail_mousewheel(event) -> None:
            delta = getattr(event, "delta", 0)
            if delta:
                detail_canvas.yview_scroll(int(-delta / 120), "units")
            else:
                num = getattr(event, "num", None)
                if num == 5:
                    detail_canvas.yview_scroll(1, "units")
                elif num == 4:
                    detail_canvas.yview_scroll(-1, "units")

        detail_canvas.bind("<MouseWheel>", _on_detail_mousewheel)
        detail_canvas.bind("<Button-4>", _on_detail_mousewheel)
        detail_canvas.bind("<Button-5>", _on_detail_mousewheel)
        detail_canvas.bind("<Configure>", lambda event: detail_canvas.itemconfigure(detail_window, width=event.width))


        self.node_tree = ttk.Treeview(
            tree_frame,
            columns=("attribute", "class", "children"),
            show="tree headings",
        )
        self.node_tree.heading("attribute", text="Attribute")
        self.node_tree.heading("class", text="Class")
        self.node_tree.heading("children", text="Children")
        self.node_tree.column("attribute", width=180, anchor=tk.W)
        self.node_tree.column("class", width=180, anchor=tk.W)
        self.node_tree.column("children", width=80, anchor=tk.CENTER)
        self.node_tree.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.node_tree.yview)
        self.node_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        detail_title = ttk.Label(detail_frame, text="Node Details", font=("Helvetica", 14, "bold"))
        detail_title.pack(anchor=tk.W)

        self.node_detail_vars: Dict[str, tk.StringVar] = {
            "name": tk.StringVar(value=""),
            "attribute_type": tk.StringVar(value=""),
            "attribute_class": tk.StringVar(value=""),
            "child_count": tk.StringVar(value=""),
            "translation": tk.StringVar(value=""),
            "rotation": tk.StringVar(value=""),
            "scaling": tk.StringVar(value=""),
        }
        self.node_properties_var = tk.StringVar(value="")

        detail_rows = ttk.Frame(detail_frame)
        detail_rows.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        detail_rows.columnconfigure(0, weight=1)

        for idx, (label, var) in enumerate(self.node_detail_vars.items()):
            row = ttk.Frame(detail_rows)
            row.grid(row=idx, column=0, sticky=tk.W, pady=3)
            ttk.Label(row, text=f"{label.replace('_', ' ').title()}:").pack(side=tk.LEFT)
            ttk.Label(row, textvariable=var, font=("Helvetica", 10, "bold"), wraplength=420).pack(
                side=tk.LEFT, padx=4
            )

        properties_row = len(self.node_detail_vars)
        properties_label = ttk.Label(detail_rows, text="User Properties:")
        properties_label.grid(row=properties_row, column=0, sticky=tk.W, pady=(12, 3))
        ttk.Label(
            detail_rows,
            textvariable=self.node_properties_var,
            font=("Helvetica", 10),
            wraplength=420,
            justify=tk.LEFT,
        ).grid(row=properties_row + 1, column=0, sticky=tk.W)

        edit_row = properties_row + 2
        edit_frame = ttk.LabelFrame(detail_rows, text="Edit Node", padding=8)
        edit_frame.grid(row=edit_row, column=0, sticky=tk.EW, pady=(12, 0))
        edit_frame.columnconfigure(1, weight=1)

        ttk.Label(edit_frame, text="Attribute Type:").grid(row=0, column=0, sticky=tk.W)
        self.attribute_combo = ttk.Combobox(
            edit_frame,
            textvariable=self._attribute_choice,
            values=self._attribute_options,
            state="readonly",
            width=18,
        )
        self.attribute_combo.grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))
        ttk.Button(edit_frame, text="Apply Attribute", command=self._apply_attribute_change).grid(
            row=0, column=2, padx=(6, 0)
        )

        ttk.Label(edit_frame, text="Rename Node:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(edit_frame, textvariable=self._node_name_edit).grid(
            row=1, column=1, sticky=tk.EW, padx=(6, 0), pady=(8, 0)
        )
        ttk.Button(edit_frame, text="Apply Name", command=self._rename_selected_node).grid(
            row=1, column=2, sticky=tk.EW, padx=(6, 0), pady=(8, 0)
        )

        ttk.Label(edit_frame, text="Translation (x, y, z):").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(edit_frame, textvariable=self._translation_edit).grid(
            row=2, column=1, sticky=tk.EW, padx=(6, 0)
        )
        ttk.Button(edit_frame, text="Apply Translation", command=lambda: self._apply_transform_edit("translation")).grid(
            row=2, column=2, sticky=tk.EW, padx=(6, 0)
        )

        ttk.Label(edit_frame, text="Rotation (x, y, z):").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(edit_frame, textvariable=self._rotation_edit).grid(
            row=3, column=1, sticky=tk.EW, padx=(6, 0), pady=(8, 0)
        )
        ttk.Button(edit_frame, text="Apply Rotation", command=lambda: self._apply_transform_edit("rotation")).grid(
            row=3, column=2, sticky=tk.EW, padx=(6, 0), pady=(8, 0)
        )

        ttk.Label(edit_frame, text="Scaling (x, y, z):").grid(row=4, column=0, sticky=tk.W)
        ttk.Entry(edit_frame, textvariable=self._scaling_edit).grid(
            row=4, column=1, sticky=tk.EW, padx=(6, 0)
        )
        ttk.Button(edit_frame, text="Apply Scaling", command=lambda: self._apply_transform_edit("scaling")).grid(
            row=4, column=2, sticky=tk.EW, padx=(6, 0)
        )

        ttk.Label(edit_frame, text="New Node Name:").grid(row=5, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(edit_frame, textvariable=self._new_node_name).grid(
            row=5, column=1, columnspan=2, sticky=tk.EW, padx=(6, 0), pady=(8, 0)
        )

        ttk.Label(edit_frame, text="New Node Type:").grid(row=6, column=0, sticky=tk.W)
        self.new_node_attribute_combo = ttk.Combobox(
            edit_frame,
            textvariable=self._new_node_attribute,
            values=self._attribute_options,
            state="normal",
            width=18,
        )
        self.new_node_attribute_combo.grid(row=6, column=1, sticky=tk.EW, padx=(6, 0))
        ttk.Button(edit_frame, text="Add Child Node", command=self._add_child_node).grid(
            row=6, column=2, sticky=tk.EW, padx=(6, 0)
        )

        ttk.Label(edit_frame, text="Reparent target:").grid(row=7, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Label(edit_frame, textvariable=self._reparent_target_var).grid(
            row=7, column=1, sticky=tk.W, padx=(6, 0), pady=(8, 0)
        )
        ttk.Button(edit_frame, text="Mark Target", command=self._mark_reparent_target).grid(
            row=7, column=2, padx=(6, 0), pady=(8, 0)
        )

        ttk.Button(
            edit_frame,
            text="Reparent Selected to Target",
            command=self._reparent_to_target,
        ).grid(row=8, column=0, columnspan=3, sticky=tk.EW, pady=(8, 0))
        ttk.Button(
            edit_frame,
            text="Promote Selected (Detach Parent)",
            command=self._promote_selected,
        ).grid(row=9, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0))
        ttk.Button(
            edit_frame,
            text="Remove Node (Promote Children)",
            command=self._remove_node_promote_children,
        ).grid(row=10, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0))

        ttk.Label(edit_frame, textvariable=self._node_status_var, foreground="#555555", wraplength=420).grid(
            row=11, column=0, columnspan=3, sticky=tk.W, pady=(8, 0)
        )

        metadata_pane = ttk.Panedwindow(detail_frame, orient=tk.VERTICAL)
        metadata_pane.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        property_frame = ttk.LabelFrame(metadata_pane, text="FBX Properties", padding=8)
        connection_frame = ttk.LabelFrame(metadata_pane, text="Connections", padding=8)
        metadata_pane.add(property_frame, weight=3)
        metadata_pane.add(connection_frame, weight=2)

        self.node_property_tree = ttk.Treeview(
            property_frame,
            columns=("category", "name", "type", "value", "flags"),
            show="headings",
        )
        for column, heading, width, anchor in [
            ("category", "Category", 110, tk.W),
            ("name", "Name", 160, tk.W),
            ("type", "Type", 140, tk.W),
            ("value", "Value", 240, tk.W),
            ("flags", "Flags", 140, tk.W),
        ]:
            self.node_property_tree.heading(column, text=heading)
            self.node_property_tree.column(column, width=width, anchor=anchor, stretch=True)
        property_scrollbar = ttk.Scrollbar(property_frame, orient=tk.VERTICAL, command=self.node_property_tree.yview)
        self.node_property_tree.configure(yscrollcommand=property_scrollbar.set)
        self.node_property_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        property_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.node_connection_tree = ttk.Treeview(
            connection_frame,
            columns=("direction", "name", "class", "uid"),
            show="headings",
        )
        for column, heading, width, anchor in [
            ("direction", "Direction", 120, tk.W),
            ("name", "Name", 180, tk.W),
            ("class", "Class", 180, tk.W),
            ("uid", "UID", 140, tk.W),
        ]:
            self.node_connection_tree.heading(column, text=heading)
            self.node_connection_tree.column(column, width=width, anchor=anchor, stretch=True)
        connection_scrollbar = ttk.Scrollbar(connection_frame, orient=tk.VERTICAL, command=self.node_connection_tree.yview)
        self.node_connection_tree.configure(yscrollcommand=connection_scrollbar.set)
        self.node_connection_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        connection_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.node_tree.bind("<<TreeviewSelect>>", self._on_node_select)
        self._render_scene_tree(scene_graph, focus_uid=None, focus_node=None)

    def _build_metadata_tab(self, container: ttk.Frame) -> None:
        metadata_notebook = ttk.Notebook(container)
        metadata_notebook.pack(fill=tk.BOTH, expand=True)

        global_frame = ttk.Frame(metadata_notebook, padding=8)
        metadata_notebook.add(global_frame, text="Global Settings")
        self.global_settings_tree = self._create_property_tree(global_frame)
        self._populate_metadata_tree(self.global_settings_tree, self.metadata.global_settings, "Global")

        document_frame = ttk.Frame(metadata_notebook, padding=8)
        metadata_notebook.add(document_frame, text="Document Info")
        self.document_info_tree = self._create_property_tree(document_frame)
        self._populate_metadata_tree(self.document_info_tree, self.metadata.document_info, "Document")

        definitions_frame = ttk.Frame(metadata_notebook, padding=8)
        metadata_notebook.add(definitions_frame, text="Definitions")
        self.definitions_tree = ttk.Treeview(definitions_frame, columns=("class", "count"), show="headings")
        self.definitions_tree.heading("class", text="Class")
        self.definitions_tree.heading("count", text="Count")
        self.definitions_tree.column("class", width=260, anchor=tk.W)
        self.definitions_tree.column("count", width=120, anchor=tk.CENTER)
        definitions_scroll = ttk.Scrollbar(definitions_frame, orient=tk.VERTICAL, command=self.definitions_tree.yview)
        self.definitions_tree.configure(yscrollcommand=definitions_scroll.set)
        self.definitions_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        definitions_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if self.metadata.definitions:
            for definition in self.metadata.definitions:
                self.definitions_tree.insert("", tk.END, values=(definition.class_name, definition.object_count))
        else:
            self.definitions_tree.insert("", tk.END, values=("<none>", ""))

        objects_frame = ttk.Frame(metadata_notebook, padding=4)
        metadata_notebook.add(objects_frame, text="Objects")

        objects_pane = ttk.Panedwindow(objects_frame, orient=tk.HORIZONTAL)
        objects_pane.pack(fill=tk.BOTH, expand=True)

        object_list_frame = ttk.Frame(objects_pane, padding=4)
        object_detail_frame = ttk.Frame(objects_pane, padding=4)
        objects_pane.add(object_list_frame, weight=1)
        objects_pane.add(object_detail_frame, weight=2)

        self.metadata_object_tree = ttk.Treeview(
            object_list_frame, columns=("uid", "name", "class", "type"), show="headings"
        )
        for column, heading, width, anchor in [
            ("uid", "UID", 140, tk.W),
            ("name", "Name", 180, tk.W),
            ("class", "Class", 160, tk.W),
            ("type", "Type", 160, tk.W),
        ]:
            self.metadata_object_tree.heading(column, text=heading)
            self.metadata_object_tree.column(column, width=width, anchor=anchor, stretch=True)
        objects_scroll = ttk.Scrollbar(object_list_frame, orient=tk.VERTICAL, command=self.metadata_object_tree.yview)
        self.metadata_object_tree.configure(yscrollcommand=objects_scroll.set)
        self.metadata_object_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        objects_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if self.metadata.objects:
            for info in self.metadata.objects:
                self.metadata_object_tree.insert("", tk.END, values=(info.uid, info.name, info.class_name, info.type_name))
        else:
            self.metadata_object_tree.insert("", tk.END, values=("<none>", "", "", ""))

        self.metadata_object_summary_var = tk.StringVar(value="Select an object to view details.")
        ttk.Label(
            object_detail_frame, textvariable=self.metadata_object_summary_var, wraplength=520, justify=tk.LEFT
        ).pack(anchor=tk.W)

        object_detail_notebook = ttk.Notebook(object_detail_frame)
        object_detail_notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        object_properties_frame = ttk.Frame(object_detail_notebook, padding=4)
        object_detail_notebook.add(object_properties_frame, text="Properties")
        self.metadata_object_properties_tree = self._create_property_tree(object_properties_frame)

        object_connections_frame = ttk.Frame(object_detail_notebook, padding=4)
        object_detail_notebook.add(object_connections_frame, text="Connections")
        self.metadata_object_connections_tree = ttk.Treeview(
            object_connections_frame, columns=("direction", "name", "class", "uid"), show="headings"
        )
        for column, heading, width, anchor in [
            ("direction", "Direction", 120, tk.W),
            ("name", "Name", 200, tk.W),
            ("class", "Class", 180, tk.W),
            ("uid", "UID", 140, tk.W),
        ]:
            self.metadata_object_connections_tree.heading(column, text=heading)
            self.metadata_object_connections_tree.column(column, width=width, anchor=anchor, stretch=True)
        object_conn_scroll = ttk.Scrollbar(
            object_connections_frame, orient=tk.VERTICAL, command=self.metadata_object_connections_tree.yview
        )
        self.metadata_object_connections_tree.configure(yscrollcommand=object_conn_scroll.set)
        self.metadata_object_connections_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        object_conn_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.metadata_object_tree.bind("<<TreeviewSelect>>", self._on_metadata_object_select)
        self._populate_metadata_object_details(None)



    def _create_property_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        tree = ttk.Treeview(
            parent,
            columns=("category", "name", "type", "value", "flags"),
            show="headings",
        )
        for column, heading, width, anchor in [
            ("category", "Category", 110, tk.W),
            ("name", "Name", 180, tk.W),
            ("type", "Type", 140, tk.W),
            ("value", "Value", 260, tk.W),
            ("flags", "Flags", 160, tk.W),
        ]:
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor=anchor, stretch=True)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _populate_metadata_tree(
        self, tree: ttk.Treeview, entries: List[FBXPropertyEntry], default_category: str
    ) -> None:
        rows = [
            (default_category, entry.name, entry.type_name, entry.value, ", ".join(entry.flags))
            for entry in entries
        ]
        if not rows:
            rows.append((default_category, "<none>", "", "", ""))
        self._refresh_tree(tree, rows)

    def _refresh_tree(self, tree: ttk.Treeview, rows: List[Tuple[Any, ...]]) -> None:
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", tk.END, values=row)

    def _populate_metadata_object_details(self, info: Optional[SceneObjectInfo]) -> None:
        if not hasattr(self, "metadata_object_properties_tree"):
            return
        if info is None:
            self.metadata_object_summary_var.set("Select an object to view details.")
            self._refresh_tree(self.metadata_object_properties_tree, [("FBX", "<none>", "", "", "")])
            self._refresh_tree(self.metadata_object_connections_tree, [("", "<none>", "", "")])
            return

        summary = (
            f"{info.name or '<unnamed>'} (UID {info.uid})\n"
            f"Class: {info.class_name}\n"
            f"Type: {info.type_name}"
        )
        self.metadata_object_summary_var.set(summary)

        property_rows = [
            ("FBX", entry.name, entry.type_name, entry.value, ", ".join(entry.flags))
            for entry in info.properties
        ]
        if not property_rows:
            property_rows.append(("FBX", "<none>", "", "", ""))
        self._refresh_tree(self.metadata_object_properties_tree, property_rows)

        connection_rows = [
            (conn.direction, conn.target_name, conn.target_class, str(conn.target_uid))
            for conn in (info.src_connections + info.dst_connections)
        ]
        if not connection_rows:
            connection_rows.append(("", "<none>", "", ""))
        self._refresh_tree(self.metadata_object_connections_tree, connection_rows)

    def _on_metadata_object_select(self, _event) -> None:
        if not hasattr(self, "metadata_object_tree"):
            return
        selection = self.metadata_object_tree.selection()
        if not selection:
            return
        values = self.metadata_object_tree.item(selection[0], "values")
        if not values:
            self._populate_metadata_object_details(None)
            return
        try:
            uid = int(values[0])
        except (TypeError, ValueError):
            self._populate_metadata_object_details(None)
            return
        info = self._object_metadata.get(uid)
        self._populate_metadata_object_details(info)

    def _update_node_metadata_views(self, node: SceneNode) -> None:
        if not hasattr(self, "node_property_tree"):
            return

        rows: List[Tuple[Any, ...]] = []
        info = self._object_metadata.get(node.uid) if node.uid is not None else None
        if info is not None:
            rows.extend(
                ("FBX", entry.name, entry.type_name, entry.value, ", ".join(entry.flags))
                for entry in info.properties
            )
        if node.properties:
            rows.extend(("User", key, "UserProperty", value, "") for key, value in node.properties.items())
        if not rows:
            rows.append(("", "<none>", "", "", ""))
        self._refresh_tree(self.node_property_tree, rows)

        connection_rows: List[Tuple[Any, ...]] = []
        if info is not None:
            connection_rows.extend(
                (conn.direction, conn.target_name, conn.target_class, str(conn.target_uid))
                for conn in (info.src_connections + info.dst_connections)
            )
        if not connection_rows:
            connection_rows.append(("", "<none>", "", ""))
        self._refresh_tree(self.node_connection_tree, connection_rows)

    def _on_node_select(self, _event) -> None:
        if not hasattr(self, "node_tree"):
            return
        node = self._get_selected_scene_node()
        if node is None:
            return

        self.node_detail_vars["name"].set(node.name)
        self.node_detail_vars["attribute_type"].set(node.attribute_type)
        self.node_detail_vars["attribute_class"].set(node.attribute_class)
        self.node_detail_vars["child_count"].set(str(node.child_count))
        self.node_detail_vars["translation"].set(_vector_to_string(node.translation))
        self.node_detail_vars["rotation"].set(_vector_to_string(node.rotation))
        self.node_detail_vars["scaling"].set(_vector_to_string(node.scaling))

        self._node_name_edit.set(node.name)
        self._translation_edit.set(_vector_to_string(node.translation))
        self._rotation_edit.set(_vector_to_string(node.rotation))
        self._scaling_edit.set(_vector_to_string(node.scaling))

        if node.properties:
            lines = [f"{key}: {value}" for key, value in node.properties.items()]
            self.node_properties_var.set("\n".join(lines))
        else:
            self.node_properties_var.set("<none>")

        if node.attribute_type and node.attribute_type not in self._attribute_options:
            self._attribute_options.append(node.attribute_type)
            self.attribute_combo.configure(values=self._attribute_options)
            self.new_node_attribute_combo.configure(values=self._attribute_options)
        self._attribute_choice.set(node.attribute_type)
        if node.attribute_type in self._attribute_options:
            self._new_node_attribute.set(node.attribute_type)
        else:
            self._new_node_attribute.set(self._attribute_options[0])
        if self._reparent_target and not self._node_exists(self._reparent_target):
            self._reparent_target = None
            self._reparent_target_var.set("<none>")
        if self._reparent_target is node:
            self._reparent_target_var.set(node.name)

        self._update_node_metadata_views(node)
        self._set_node_status("")

    def _get_selected_scene_node(self) -> Optional[SceneNode]:
        if not hasattr(self, "node_tree"):
            return None
        selection = self.node_tree.selection()
        if not selection:
            return None
        return self._node_map.get(selection[0])

    def _rename_selected_node(self) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to rename.")
            return

        new_name = self._node_name_edit.get().strip()
        if not new_name:
            self._set_node_status("Enter a name before applying.")
            return

        node.name = new_name
        self._mark_scene_graph_dirty()
        if self._reparent_target is node:
            self._reparent_target_var.set(new_name)

        self.node_detail_vars["name"].set(new_name)
        self._refresh_scene_tree(focus_uid=node.uid)
        self._set_node_status(f"Renamed node to {new_name}.")

    def _apply_attribute_change(self) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to edit.")
            return
        new_type = self._attribute_choice.get().strip()
        if not new_type:
            self._set_node_status("Choose an attribute type before applying.")
            return
        node.attribute_type = new_type
        if node.attribute_class == "(NoAttribute)" and new_type in self._attribute_options:
            node.attribute_class = "Skeleton"
        self._mark_scene_graph_dirty()
        self._refresh_scene_tree(focus_uid=node.uid)
        self._set_node_status(f"Updated attribute to {new_type}.")

    def _derive_attribute_class(self, attribute_type: str) -> str:
        skeleton_types = {"Root", "Limb", "LimbNode", "Effector"}
        if attribute_type in skeleton_types:
            return "Skeleton"
        if attribute_type == "Node":
            return "(NoAttribute)"
        if attribute_type:
            return attribute_type
        return "(NoAttribute)"

    def _parse_vector_input(self, value: str) -> Optional[Tuple[float, float, float]]:
        parts = [part for part in value.replace(",", " ").split() if part]
        if len(parts) != 3:
            return None
        try:
            vector = tuple(float(part) for part in parts)
        except ValueError:
            return None
        return cast(Tuple[float, float, float], vector)

    def _apply_transform_edit(self, attribute: str) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to edit.")
            return

        attr_map = {
            "translation": self._translation_edit,
            "rotation": self._rotation_edit,
            "scaling": self._scaling_edit,
        }
        var = attr_map.get(attribute)
        if var is None:
            return

        label = attribute.capitalize()
        vector = self._parse_vector_input(var.get().strip())
        if vector is None:
            self._set_node_status(f"Enter three numeric values for {label.lower()}.")
            return

        setattr(node, attribute, vector)
        self._mark_scene_graph_dirty()
        self.node_detail_vars[attribute].set(_vector_to_string(vector))
        var.set(_vector_to_string(vector))

        self._refresh_scene_tree(focus_uid=node.uid)
        self._set_node_status(f"Updated {label.lower()} for {node.name}.")

    def _add_child_node(self) -> None:
        parent = self._get_selected_scene_node()
        if parent is None:
            self._set_node_status("Select a parent node before adding a child.")
            return

        name = self._new_node_name.get().strip() or "NewNode"
        attribute_type = self._new_node_attribute.get().strip() or "Node"
        attribute_class = self._derive_attribute_class(attribute_type)

        existing_names = {child.name for child in parent.children}
        unique_name = name
        suffix = 1
        while unique_name in existing_names:
            suffix += 1
            unique_name = f"{name}_{suffix}"

        new_node = SceneNode(
            name=unique_name,
            attribute_type=attribute_type,
            attribute_class=attribute_class,
            translation=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            scaling=(1.0, 1.0, 1.0),
            child_count=0,
            uid=None,
            parent_uid=parent.uid,
            original_path=(),
            properties={},
        )
        parent.children.append(new_node)
        self._mark_scene_graph_dirty()

        self._new_node_name.set("")
        self._refresh_scene_tree(focus_node=new_node)
        self._set_node_status(f"Added {new_node.name} under {parent.name}.")

    def _mark_reparent_target(self) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to mark as target.")
            return
        self._reparent_target = node
        self._reparent_target_var.set(node.name)
        self._set_node_status(f"Marked {node.name} as reparent target.")

    def _reparent_to_target(self) -> None:
        node = self._get_selected_scene_node()
        target = self._reparent_target
        if node is None:
            self._set_node_status("Select a node to move.")
            return
        if target is None:
            self._set_node_status("Mark a reparent target first.")
            return
        if node is target:
            self._set_node_status("Cannot reparent a node to itself.")
            return
        if self._is_descendant(node, target):
            self._set_node_status("Cannot reparent to a descendant; that would create a cycle.")
            return
        parent = self._find_parent(self.document.scene_graph, node)
        if parent is None:
            self._set_node_status("Cannot reparent the root node.")
            return
        parent.children.remove(node)
        target.children.append(node)
        self._mark_scene_graph_dirty()
        self._refresh_scene_tree(focus_uid=node.uid)
        self._set_node_status(f"Moved {node.name} under {target.name}.")

    def _promote_selected(self) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to promote.")
            return
        parent = self._find_parent(self.document.scene_graph, node)
        if parent is None:
            self._set_node_status("Selected node is already the root.")
            return
        grandparent = self._find_parent(self.document.scene_graph, parent)
        if grandparent is None:
            self._set_node_status("Use 'Remove Node' to replace the root instead.")
            return
        parent.children.remove(node)
        grandparent.children.append(node)
        self._mark_scene_graph_dirty()
        self._refresh_scene_tree(focus_uid=node.uid)
        self._set_node_status(f"Promoted {node.name} to be a child of {grandparent.name}.")

    def _remove_node_promote_children(self) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to remove.")
            return
        root = self.document.scene_graph
        parent = self._find_parent(root, node)
        focus_uid: Optional[int] = None
        if parent is None:
            if not node.children:
                self.document.scene_graph = None
                self._set_node_status(f"Removed root node {node.name}; scene is now empty.")
            else:
                new_root = node.children[0]
                for child in node.children[1:]:
                    new_root.children.append(child)
                self.document.scene_graph = new_root
                focus_uid = new_root.uid
                self._set_node_status(f"Removed root {node.name}; promoted {new_root.name} to root.")
            self._reset_reparent_target()
        else:
            parent.children.remove(node)
            parent.children.extend(node.children)
            focus_uid = parent.uid
            self._set_node_status(
                f"Removed {node.name}; promoted its children under {parent.name}."
            )

        self._mark_scene_graph_dirty()
        if self._reparent_target is node:
            self._reset_reparent_target()

        self._refresh_scene_tree(focus_uid=focus_uid)

    def _find_parent(self, current: Optional[SceneNode], target: SceneNode) -> Optional[SceneNode]:
        if current is None or current is target:
            return None
        for child in current.children:
            if child is target:
                return current
            result = self._find_parent(child, target)
            if result is not None:
                return result
        return None

    def _is_descendant(self, ancestor: SceneNode, candidate: SceneNode) -> bool:
        if ancestor is candidate:
            return True
        for child in ancestor.children:
            if self._is_descendant(child, candidate):
                return True
        return False

    def _node_exists(self, node: SceneNode) -> bool:
        root = self.document.scene_graph
        return root is not None and self._is_descendant(root, node)

    def _rebuild_parent_links(self, node: SceneNode, parent: Optional[SceneNode] = None) -> None:
        node.parent_uid = parent.uid if parent else None
        for child in node.children:
            self._rebuild_parent_links(child, node)

    def _recompute_child_counts(self, node: SceneNode) -> int:
        node.child_count = len(node.children)
        for child in node.children:
            self._recompute_child_counts(child)
        return node.child_count

    def _update_document_top_level(self) -> None:
        root = self.document.scene_graph
        top_level: List[Dict[str, Any]] = []
        if root:
            for child in root.children:
                top_level.append(
                    {
                        "name": child.name,
                        "attribute_type": child.attribute_type,
                        "attribute_class": child.attribute_class,
                        "child_count": child.child_count,
                        "is_mesh": "mesh" in child.attribute_type.lower()
                        or (child.attribute_class or "").lower().endswith("mesh"),
                    }
                )
        self.document.top_level_nodes = top_level

    def _set_node_status(self, message: str) -> None:
        self._node_status_var.set(message)

    def _mark_scene_graph_dirty(self) -> None:
        """Flag the document so Save As knows edits were applied."""

        self.document.scene_graph_dirty = True

    def update_document_path(self, new_path: str) -> None:
        self.document.path = str(new_path)
        display_name = Path(self.document.path).name or self.document.path
        self._display_name_var.set(f"File: {display_name}")
        self._full_path_var.set(self.document.path)

    def _refresh_scene_tree(
        self,
        *,
        focus_uid: Optional[int] = None,
        focus_node: Optional[SceneNode] = None,
    ) -> None:
        """Rebuild the tree view after mutating the in-memory scene graph."""

        self._update_document_top_level()
        scene_graph = self.document.scene_graph
        if scene_graph is None:
            self._clear_scene_tree_view()
            return

        self._render_scene_tree(scene_graph, focus_uid=focus_uid, focus_node=focus_node)

    def _render_scene_tree(
        self,
        scene_graph: SceneNode,
        *,
        focus_uid: Optional[int],
        focus_node: Optional[SceneNode],
    ) -> None:
        """Populate the tree widget from the provided ``SceneNode`` hierarchy."""

        self._rebuild_parent_links(scene_graph)
        self._recompute_child_counts(scene_graph)

        self._node_map.clear()
        self.node_tree.delete(*self.node_tree.get_children(""))

        focus_item: Optional[str] = None

        def insert(parent: str, node: SceneNode) -> None:
            nonlocal focus_item
            node_id = self.node_tree.insert(
                parent,
                tk.END,
                text=node.name,
                values=(node.attribute_type, node.attribute_class, node.child_count),
            )
            self._node_map[node_id] = node
            if focus_uid is not None and node.uid == focus_uid:
                focus_item = node_id
            if focus_node is node:
                focus_item = node_id
            for child in node.children:
                insert(node_id, child)

        insert("", scene_graph)
        root_items = self.node_tree.get_children("")
        for child_id in root_items:
            self.node_tree.item(child_id, open=True)

        if focus_item:
            self.node_tree.selection_set(focus_item)
        elif root_items:
            self.node_tree.selection_set(root_items[0])
        else:
            self.node_tree.selection_remove(self.node_tree.selection())

        self._on_node_select(None)

    def _clear_scene_tree_view(self) -> None:
        """Reset the tree widget when the scene graph becomes empty."""

        if not hasattr(self, "node_tree"):
            return
        self.node_tree.delete(*self.node_tree.get_children(""))
        self._node_map.clear()
        self.node_properties_var.set("<none>")
        for var in self.node_detail_vars.values():
            var.set("")

    def _reset_reparent_target(self) -> None:
        """Clear the currently marked reparent target."""

        self._reparent_target = None
        self._reparent_target_var.set("<none>")

class FBXAnalyzerApp:
    """Main window that manages multiple FBX analyses."""

    def __init__(self, documents: Iterable[AnalyzedScene]) -> None:
        self.documents: List[AnalyzedScene] = list(documents)

        self.root = tk.Tk()
        self.root.title("FBX Analyzer")
        self.root.geometry("1600x1000")

        self.status_var = tk.StringVar(value="Load FBX files to analyze.")
        self._debug_mode = tk.BooleanVar(value=False)

        self._build_ui()

        self.document_tabs: Dict[str, DocumentPane] = {}
        for document in self.documents:
            self._add_document_tab(document)

        self._update_status()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        import_button = ttk.Button(toolbar, text="Import FBX...", command=self._on_import_clicked)
        import_button.pack(side=tk.LEFT)

        save_button = ttk.Button(toolbar, text="Save As...", command=self._on_save_as_clicked)
        save_button.pack(side=tk.LEFT, padx=(6, 0))

        debug_toggle = ttk.Checkbutton(toolbar, text="Debug Mode", variable=self._debug_mode)
        debug_toggle.pack(side=tk.LEFT, padx=(6, 0))

        close_button = ttk.Button(toolbar, text="Close Tab", command=self._on_close_clicked)
        close_button.pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.LEFT, padx=(12, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

    def _on_import_clicked(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Select FBX file(s)",
            filetypes=[("FBX files", "*.fbx"), ("All files", "*.*")],
        )
        if not paths:
            return

        for path in paths:
            document = self._analyze_file(path)
            if document is None:
                continue
            self.documents.append(document)
            self._add_document_tab(document)

    def _on_close_clicked(self) -> None:
        tab_id = self.notebook.select()
        if not tab_id:
            return

        pane = self.document_tabs.pop(tab_id, None)
        if pane is None:
            return

        document = pane.document
        try:
            self.documents.remove(document)
        except ValueError:
            pass

        self.notebook.forget(tab_id)
        pane.frame.destroy()
        self._update_status()

    def _on_save_as_clicked(self) -> None:
        tab_id = self.notebook.select()
        if not tab_id:
            messagebox.showinfo("FBX Analyzer", "Load an FBX file before saving.", parent=self.root)
            return

        pane = self.document_tabs.get(tab_id)
        if pane is None:
            messagebox.showinfo("FBX Analyzer", "Load an FBX file before saving.", parent=self.root)
            return

        current_path = Path(pane.document.path)
        initial_dir = current_path.parent if current_path.parent.exists() else Path.cwd()
        save_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save FBX As...",
            defaultextension=".fbx",
            filetypes=[("FBX files", "*.fbx"), ("All files", "*.*")],
            initialdir=str(initial_dir),
            initialfile=current_path.name,
        )
        if not save_path:
            return

        debug_log_path: Optional[Path] = None
        try:
            if self._debug_mode.get():
                diagnostics = rebuild_scene_graph_as(
                    pane.document.path,
                    save_path,
                    pane.document.scene_graph,
                    diagnostics=SceneExportDiagnostics(),
                )
                debug_log_path = self._write_debug_diagnostics(Path(save_path), diagnostics)
            else:
                scene_graph = pane.document.scene_graph if pane.document.scene_graph_dirty else None
                save_scene_graph_as(pane.document.path, save_path, scene_graph)
        except (FBXSDKNotAvailableError, FBXLoadError, FBXSaveError) as exc:
            messagebox.showerror("FBX Analyzer", str(exc), parent=self.root)
            return

        pane.document.scene_graph_dirty = False
        pane.update_document_path(save_path)
        if self._debug_mode.get():
            if debug_log_path is not None:
                self.status_var.set(
                    f"Saved debug copy to {save_path}; diagnostics -> {debug_log_path}"
                )
            else:
                self.status_var.set(
                    f"Saved debug copy to {save_path}; failed to write diagnostics log."
                )
        else:
            self.status_var.set(f"Saved copy to {save_path}")

    def _write_debug_diagnostics(
        self, save_path: Path, diagnostics: SceneExportDiagnostics
    ) -> Optional[Path]:
        debug_path = save_path.with_suffix(save_path.suffix + ".debug.json")
        try:
            debug_path.write_text(
                json.dumps(asdict(diagnostics), indent=2),
                encoding="utf-8",
            )
            return debug_path
        except Exception:
            return None

    def _analyze_file(self, path: str) -> Optional[AnalyzedScene]:
        skeleton_inspector = SkeletonInspector()
        top_level_inspector = TopLevelInspector()
        scene_graph_inspector = SceneGraphInspector()
        metadata_inspector = SceneMetadataInspector()

        try:
            with FBXAnalyzer(str(path)) as analyzer:
                results = analyzer.run([
                    skeleton_inspector,
                    top_level_inspector,
                    scene_graph_inspector,
                    metadata_inspector,
                ])
        except (FBXSDKNotAvailableError, FBXLoadError) as exc:
            messagebox.showerror("FBX Analyzer", str(exc), parent=self.root)
            return None

        skeletons = results.get(skeleton_inspector.id, [])
        top_level_nodes = results.get(top_level_inspector.id, []) or []
        scene_graph = results.get(scene_graph_inspector.id)
        metadata = results.get(metadata_inspector.id) or SceneMetadata()

        return AnalyzedScene(
            path=str(path),
            skeletons=skeletons,
            scene_graph=scene_graph,
            top_level_nodes=top_level_nodes,
            metadata=metadata,
        )

    def _add_document_tab(self, document: AnalyzedScene) -> None:
        pane = DocumentPane(self.notebook, document)
        tab_text = Path(document.path).name or document.path
        self.notebook.add(pane.frame, text=tab_text)
        tab_id = str(pane.frame)
        self.document_tabs[tab_id] = pane
        self.notebook.select(pane.frame)
        self._update_status()

    def _update_status(self) -> None:
        count = len(self.document_tabs)
        if count:
            suffix = "s" if count != 1 else ""
            self.status_var.set(f"Loaded {count} file{suffix}")
        else:
            self.status_var.set("No FBX files loaded. Use 'Import FBX...' to begin.")

    def run(self) -> None:
        if not self.documents:
            self.status_var.set("No FBX files loaded. Use 'Import FBX...' to begin.")
        self.root.mainloop()


def launch_skeleton_viewer(documents: List[AnalyzedScene]) -> None:
    app = FBXAnalyzerApp(documents)
    app.run()


def _vector_to_string(vector: Iterable[float]) -> str:
    return ", ".join(f"{component:.3f}" for component in vector)
















