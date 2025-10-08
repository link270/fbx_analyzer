from fbx_analyzer.core import sdk
mgr=sdk.create_manager(); sdk.create_io_settings(mgr); sc=sdk.create_scene(mgr)
sdk.load_scene(mgr, sc, r"examples/example_animation_conformed_new.fbx")
root=sc.GetRootNode()
for i in range(root.GetChildCount()):
    child=root.GetChild(i)
    print(child.GetName(), 'children', child.GetChildCount())
    for j in range(child.GetChildCount()):
        print('  ->', child.GetChild(j).GetName(), 'parent', child.GetName())

sdk.destroy_manager(mgr)
