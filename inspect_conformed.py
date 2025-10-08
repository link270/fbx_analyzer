from fbx_analyzer.core import sdk
mgr=sdk.create_manager(); sdk.create_io_settings(mgr); sc=sdk.create_scene(mgr)
sdk.load_scene(mgr, sc, r"examples/example_animation_conformed_new.fbx")
fbx,_=sdk.import_fbx_module()
root=sc.GetRootNode()
print('Scene root children:')
for i in range(root.GetChildCount()):
    child=root.GetChild(i)
    attr=child.GetNodeAttribute()
    print(f"  {child.GetName()} type={attr.__class__.__name__ if attr else None}")
stack=[root]
while stack:
    node=stack.pop()
    attr=node.GetNodeAttribute()
    if attr and isinstance(attr, fbx.FbxSkeleton):
        parent = node.GetParent()
        print(f"Bone {node.GetName()} type={attr.GetSkeletonType()} parent={(parent.GetName() if parent else None)}")
    for idx in range(node.GetChildCount()):
        stack.append(node.GetChild(idx))
