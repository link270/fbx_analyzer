from fbx_analyzer.core import sdk
fbx,_=sdk.import_fbx_module()
print('EType exists', hasattr(fbx.FbxSkeleton, 'EType'))
print('dir EType', dir(getattr(fbx.FbxSkeleton, 'EType')))
etype = fbx.FbxSkeleton.EType
print('values', etype.eRoot, etype.eLimb, etype.eLimbNode, etype.eEffector)
