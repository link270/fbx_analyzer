"""Common dialog utilities for the GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Optional


def ask_for_fbx_file(initial_dir: Optional[str] = None) -> Optional[str]:
    """Show a file picker for FBX files and return the selected path."""

    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Select FBX file",
        filetypes=[("FBX files", "*.fbx"), ("All files", "*.*")],
        initialdir=initial_dir or "",
    )
    root.destroy()
    return file_path or None
