from fbx_analyzer.core import sdk
fbx,_ = sdk.import_fbx_module()
scene = sdk.create_scene(sdk.create_manager())
node = fbx.FbxSkeleton.Create(scene, 'test')
print('default', node.GetSkeletonType())
node.SetSkeletonType(fbx.FbxSkeleton.EType.eRoot)
print('after root', node.GetSkeletonType())
node.SetSkeletonType(fbx.FbxSkeleton.EType.eEffector)
print('after effector', node.GetSkeletonType())
