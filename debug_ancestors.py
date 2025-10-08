from fbx_analyzer.core import sdk
mgr = sdk.create_manager(); sdk.create_io_settings(mgr); scene = sdk.create_scene(mgr)
sdk.load_scene(mgr, scene, r"examples/example_animation.fbx")
fbx,_ = sdk.import_fbx_module()
root=scene.GetRootNode()
# find Hips
stack=[root]
hips=None
while stack:
    node=stack.pop()
    if (node.GetName() or "") == "Hips":
        hips=node; break
    for i in range(node.GetChildCount()):
        stack.append(node.GetChild(i))
ancestors=[]
walker=hips
while walker is not None and walker is not root:
    parent=walker.GetParent()
    if parent is None:
        break
    ancestors.append(parent)
    if parent is root:
        break
    walker=parent
print([n.GetName() for n in ancestors])
