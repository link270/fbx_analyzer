from fbx_analyzer.core import sdk
mgr=sdk.create_manager(); sdk.create_io_settings(mgr); sc=sdk.create_scene(mgr)
sdk.load_scene(mgr, sc, r"examples/example_character.fbx")
fbx,_=sdk.import_fbx_module()
root=sc.GetRootNode()
for i in range(root.GetChildCount()):
    child=root.GetChild(i)
    attr=child.GetNodeAttribute()
    print(child.GetName(), attr.__class__.__name__ if attr else None)
stack=[root]
while stack:
    node=stack.pop()
    if node.GetName()=="Hips":
        print('Skeleton Hips parent', node.GetParent().GetName() if node.GetParent() else None)
    for idx in range(node.GetChildCount()):
        stack.append(node.GetChild(idx))

sdk.destroy_manager(mgr)
