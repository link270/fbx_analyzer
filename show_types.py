from fbx_analyzer.tools.conform_anim_to_skeleton import _build_skeleton_map
from fbx_analyzer.core import sdk
fbx,_=sdk.import_fbx_module()
print('EType', fbx.FbxSkeleton.EType.eRoot, fbx.FbxSkeleton.EType.eLimbNode, fbx.FbxSkeleton.EType.eEffector)
