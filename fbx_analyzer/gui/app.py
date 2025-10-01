"""Tkinter GUI for presenting FBX information."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional

from ..core import FBXAnalyzer
from ..core.exceptions import FBXLoadError, FBXSDKNotAvailableError, FBXSaveError
from ..core.save_as import save_scene_graph_as
from ..inspectors import SceneGraphInspector, SkeletonInspector, TopLevelInspector
from ..models import AnalyzedScene, Joint, SceneNode, Skeleton


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
        self._attribute_options = ["Root", "Limb", "LimbNode", "Effector", "Node"]
        self._pending_focus_uid: Optional[int] = None

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
        detail_frame = ttk.Frame(main_pane, padding=8)
        main_pane.add(tree_frame, weight=1)
        main_pane.add(detail_frame, weight=2)

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
        detail_rows.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

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

        self._attribute_choice = tk.StringVar(value="")
        self._reparent_target_var = tk.StringVar(value="<none>")
        self._node_status_var = tk.StringVar(value="")

        main_pane = ttk.Panedwindow(container, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(main_pane, padding=8)
        detail_frame = ttk.Frame(main_pane, padding=8)
        main_pane.add(tree_frame, weight=1)
        main_pane.add(detail_frame, weight=2)

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

        ttk.Label(edit_frame, text="Reparent target:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Label(edit_frame, textvariable=self._reparent_target_var).grid(
            row=1, column=1, sticky=tk.W, padx=(6, 0), pady=(8, 0)
        )
        ttk.Button(edit_frame, text="Mark Target", command=self._mark_reparent_target).grid(
            row=1, column=2, padx=(6, 0), pady=(8, 0)
        )

        ttk.Button(
            edit_frame,
            text="Reparent Selected to Target",
            command=self._reparent_to_target,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(8, 0))
        ttk.Button(
            edit_frame,
            text="Promote Selected (Detach Parent)",
            command=self._promote_selected,
        ).grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0))
        ttk.Button(
            edit_frame,
            text="Remove Node (Promote Children)",
            command=self._remove_node_promote_children,
        ).grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0))

        ttk.Label(edit_frame, textvariable=self._node_status_var, foreground="#555555", wraplength=420).grid(
            row=5, column=0, columnspan=3, sticky=tk.W, pady=(8, 0)
        )

        self.node_tree.bind("<<TreeviewSelect>>", self._on_node_select)
        self._populate_scene_tree(scene_graph)

    def _populate_scene_tree(self, scene_graph: SceneNode) -> None:
        self._rebuild_parent_links(scene_graph)
        self._recompute_child_counts(scene_graph)

        focus_uid = getattr(self, "_pending_focus_uid", None)
        self._pending_focus_uid = None

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

        if node.properties:
            lines = [f"{key}: {value}" for key, value in node.properties.items()]
            self.node_properties_var.set("\n".join(lines))
        else:
            self.node_properties_var.set("<none>")

        self._attribute_choice.set(node.attribute_type)
        if self._reparent_target and not self._node_exists(self._reparent_target):
            self._reparent_target = None
            self._reparent_target_var.set("<none>")
        if self._reparent_target is node:
            self._reparent_target_var.set(node.name)
        self._set_node_status("")

    def _get_selected_scene_node(self) -> Optional[SceneNode]:
        if not hasattr(self, "node_tree"):
            return None
        selection = self.node_tree.selection()
        if not selection:
            return None
        return self._node_map.get(selection[0])

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
        self._pending_focus_uid = node.uid
        self._update_document_top_level()
        if self.document.scene_graph:
            self._populate_scene_tree(self.document.scene_graph)
        self._set_node_status(f"Updated attribute to {new_type}.")

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
        self._pending_focus_uid = node.uid
        self._update_document_top_level()
        if self.document.scene_graph:
            self._populate_scene_tree(self.document.scene_graph)
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
        self._pending_focus_uid = node.uid
        self._update_document_top_level()
        if self.document.scene_graph:
            self._populate_scene_tree(self.document.scene_graph)
        self._set_node_status(f"Promoted {node.name} to be a child of {grandparent.name}.")

    def _remove_node_promote_children(self) -> None:
        node = self._get_selected_scene_node()
        if node is None:
            self._set_node_status("Select a node to remove.")
            return
        root = self.document.scene_graph
        parent = self._find_parent(root, node)
        if parent is None:
            if not node.children:
                self.document.scene_graph = None
                self._set_node_status(f"Removed root node {node.name}; scene is now empty.")
            else:
                new_root = node.children[0]
                for child in node.children[1:]:
                    new_root.children.append(child)
                self.document.scene_graph = new_root
                self._set_node_status(f"Removed root {node.name}; promoted {new_root.name} to root.")
            self._reparent_target = None
            self._reparent_target_var.set("<none>")
        else:
            parent.children.remove(node)
            parent.children.extend(node.children)
            self._set_node_status(
                f"Removed {node.name}; promoted its children under {parent.name}."
            )
        self._pending_focus_uid = parent.uid if parent else (self.document.scene_graph.uid if self.document.scene_graph else None)
        self._update_document_top_level()
        if self.document.scene_graph:
            self._populate_scene_tree(self.document.scene_graph)
        else:
            self.node_tree.delete(*self.node_tree.get_children(""))
            self._node_map.clear()
            self.node_properties_var.set("<none>")
            for var in self.node_detail_vars.values():
                var.set("")

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

    def update_document_path(self, new_path: str) -> None:
        self.document.path = str(new_path)
        display_name = Path(self.document.path).name or self.document.path
        self._display_name_var.set(f"File: {display_name}")
        self._full_path_var.set(self.document.path)

class FBXAnalyzerApp:
    """Main window that manages multiple FBX analyses."""

    def __init__(self, documents: Iterable[AnalyzedScene]) -> None:
        self.documents: List[AnalyzedScene] = list(documents)

        self.root = tk.Tk()
        self.root.title("FBX Analyzer")
        self.root.geometry("1100x700")

        self.status_var = tk.StringVar(value="Load FBX files to analyze.")

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

        try:
            save_scene_graph_as(pane.document.path, save_path, pane.document.scene_graph)
        except (FBXSDKNotAvailableError, FBXLoadError, FBXSaveError) as exc:
            messagebox.showerror("FBX Analyzer", str(exc), parent=self.root)
            return

        pane.update_document_path(save_path)
        self.status_var.set(f"Saved copy to {save_path}")

    def _analyze_file(self, path: str) -> Optional[AnalyzedScene]:
        skeleton_inspector = SkeletonInspector()
        top_level_inspector = TopLevelInspector()
        scene_graph_inspector = SceneGraphInspector()

        try:
            with FBXAnalyzer(str(path)) as analyzer:
                results = analyzer.run([skeleton_inspector, top_level_inspector, scene_graph_inspector])
        except (FBXSDKNotAvailableError, FBXLoadError) as exc:
            messagebox.showerror("FBX Analyzer", str(exc), parent=self.root)
            return None

        skeletons = results.get(skeleton_inspector.id, [])
        top_level_nodes = results.get(top_level_inspector.id, []) or []
        scene_graph = results.get(scene_graph_inspector.id)

        return AnalyzedScene(
            path=str(path),
            skeletons=skeletons,
            scene_graph=scene_graph,
            top_level_nodes=top_level_nodes,
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
