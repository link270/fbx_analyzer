from fbx_analyzer.core import sdk
mgr = sdk.create_manager(); sdk.create_io_settings(mgr); scene = sdk.create_scene(mgr)
if not sdk.load_scene(mgr, scene, r"examples/example_animation_patched_new.fbx"):
    raise SystemExit("load fail")
fbx,_ = sdk.import_fbx_module()
root = scene.GetRootNode()
for i in range(root.GetChildCount()):
    child = root.GetChild(i)
    attr = child.GetNodeAttribute()
    print(f"Root child {child.GetName()} type={attr.__class__.__name__ if attr else None} S={tuple(child.LclScaling.Get())}")
stack=[root]
while stack:
    node=stack.pop()
    if node.GetName()=="Hips":
        print("Hips parent", node.GetParent().GetName() if node.GetParent() else None, "scale", tuple(node.LclScaling.Get()))
    for i in range(node.GetChildCount()):
        stack.append(node.GetChild(i))
sdk.destroy_manager(mgr)
