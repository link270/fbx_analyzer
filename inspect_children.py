from fbx_analyzer.tools.conform_anim_to_skeleton import _build_skeleton_map
root_joint, container, container_trs, parent_map, joint_type_map, rest_trs_map = _build_skeleton_map('examples/example_character.fbx')
children = {}
for child, parent in parent_map.items():
    if parent is None:
        continue
    children.setdefault(parent, []).append(child)
print('children of RightToeBase', children.get('RightToeBase'))
print('children of RightToeBase_end', children.get('RightToeBase_end'))
