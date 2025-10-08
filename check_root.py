from fbx_analyzer.tools.conform_anim_to_skeleton import _build_skeleton_map
root_joint, container, container_trs, parent_map, joint_type_map, rest_trs_map = _build_skeleton_map('examples/example_character.fbx')
print('root joint', root_joint)
