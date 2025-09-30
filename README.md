# fbx_analyzer

fbx_analyzer is a Python toolkit for inspecting Autodesk FBX files. The initial focus is on skeleton data, but the architecture is designed to expose additional information over time (geometry, materials, animation clips, etc.).

## Features

- Structured analyzer core that loads FBX scenes via the Autodesk FBX SDK (Python bindings).
- Extensible visitor-style traversal engine for collecting arbitrary data from the scene graph.
- Skeleton extraction utilities that build a clean Python representation of joints, hierarchy, and transforms.
- Infers bone hierarchies from animation tracks or skin clusters when explicit skeleton nodes are missing.
- Lists top-level scene nodes so you can quickly understand what major data the FBX contains.
- Dedicated Scene Nodes GUI tab to browse every node with attribute and transform details.
- Scene Nodes editing tools to retag skeleton attributes and reparent or promote nodes for quick what-if adjustments.
- Close individual tabs to unload FBX files without restarting the analyzer.
- Simple Tkinter GUI that displays the skeleton hierarchy and joint metadata.

## Requirements

- Python 3.9+
- Autodesk FBX SDK with Python bindings (`fbx` module). Download from Autodesk and ensure the SDK's Python package is discoverable (e.g., add it to `PYTHONPATH`).

The project intentionally avoids bundling the SDK. If the module is unavailable, the application surfaces a helpful error.

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
# Ensure Autodesk FBX SDK's Python bindings are installed and available.
python -m fbx_analyzer
```

- When launched without arguments, a file picker appears so you can choose the FBX to inspect.
- Provide a path manually (e.g., `python -m fbx_analyzer path\to\file.fbx`) to skip the file dialog.
- Add `--no-gui` to print a text summary instead of opening the viewer.

If your file only stores animation curves without any rig nodes, the analyzer tries to reconstruct a hierarchy from the animation targets; if nothing references those curves, it still reports that no skeleton data was found.
The CLI prints a summary of top-level nodes before launching the GUI, which is also handy when running with --no-gui for batch inspection.
Use the Import FBX button in the GUI to load multiple files; each file opens in its own tab labelled with the source filename for easy comparison. Use the Close Tab button to remove the active file when you are done comparing.
When the GUI opens, the left pane shows the skeleton hierarchy (if the FBX contains skeleton nodes). Selecting a joint reveals its metadata (e.g., node name, type, transformation) in the right pane.

## Roadmap

- Annotate additional scene node information (meshes, materials, animations).
- Export analysis summaries to JSON.
- Add automated tests with sample FBX fixtures.

## Development

The code is split into three main pieces:

- `fbx_analyzer.core` - Loader, manager lifecycle, and traversal utilities.
- `fbx_analyzer.inspectors` - Pluggable inspectors that extract targeted data (currently skeletons).
- `fbx_analyzer.gui` - Tkinter interface to visualize collected data.

Contributions can add new inspectors or extend the GUI with more visualizations.
```
