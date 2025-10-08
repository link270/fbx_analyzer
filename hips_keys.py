from fbx_analyzer.core import sdk
mgr=sdk.create_manager(); sdk.create_io_settings(mgr); sc=sdk.create_scene(mgr)
sdk.load_scene(mgr, sc, r"examples/example_animation_patched_new.fbx")
fbx,_=sdk.import_fbx_module()
root=sc.GetRootNode()
stack=[root]
layer=None
try:
    crit = fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId)
    stack_obj = sc.GetSrcObject(crit,0)
    if stack_obj:
        layer = stack_obj.GetMember(0)
except Exception:
    layer=None
while stack:
    node=stack.pop()
    if node.GetName()=="Hips":
        print("Hips keys:")
        for prop,label in [(node.LclTranslation,'T'),(node.LclRotation,'R'),(node.LclScaling,'S')]:
            counts=[]
            for axis in 'XYZ':
                try:
                    curve=prop.GetCurve(layer, axis)
                except Exception:
                    curve=None
                counts.append(curve.KeyGetCount() if curve else 0)
            print(label, counts)
    for i in range(node.GetChildCount()):
        stack.append(node.GetChild(i))

sdk.destroy_manager(mgr)
