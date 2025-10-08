from fbx_analyzer.core import sdk
mgr = sdk.create_manager(); sdk.create_io_settings(mgr); scene = sdk.create_scene(mgr)
if not sdk.load_scene(mgr, scene, r"examples/example_animation_patched.fbx"):
    raise SystemExit("load fail")
fbx,_ = sdk.import_fbx_module()
root = scene.GetRootNode()
print("Scene root children:")
for i in range(root.GetChildCount()):
    child = root.GetChild(i)
    attr = child.GetNodeAttribute()
    print(f"- {child.GetName()} ({attr.__class__.__name__ if attr else None})")

stack = [root]
while stack:
    node = stack.pop()
    if node.GetName() in ("exampleCharacter_BODY", "Hips"):
        attr = node.GetNodeAttribute()
        print(f"Node {node.GetName()} parent={(node.GetParent().GetName() if node.GetParent() else None)} type={attr.__class__.__name__ if attr else None}")
        print("  T", tuple(node.LclTranslation.Get()))
        print("  R", tuple(node.LclRotation.Get()))
        print("  S", tuple(node.LclScaling.Get()))
    for i in range(node.GetChildCount()):
        stack.append(node.GetChild(i))

sdk.destroy_manager(mgr)
