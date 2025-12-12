import sys
sys.path.insert(0, r'o:\\Cloud\\Code\\_scripts')
sys.path.insert(0, r'o:\\Cloud\\Code\\_scripts\\mayatk')

# Reload modules to get fresh code
import importlib
if 'mayatk.anim_utils.scale_keys' in sys.modules:
    del sys.modules['mayatk.anim_utils.scale_keys']
if 'mayatk.anim_utils._anim_utils' in sys.modules:
    del sys.modules['mayatk.anim_utils._anim_utils']

try:
    from mayatk.anim_utils.scale_keys import ScaleKeys
    from mayatk.anim_utils._anim_utils import KeyframeGrouper
    print('SUCCESS: All imports work correctly')
    print(f'ScaleKeys has scale_keys: {hasattr(ScaleKeys, \"scale_keys\")}')
    print(f'KeyframeGrouper has collect_segments: {hasattr(KeyframeGrouper, \"collect_segments\")}')
except Exception as e:
    import traceback
    print(f'ERROR: {e}')
    traceback.print_exc()
