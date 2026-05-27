# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-27._

## Added (4)

- `node_utils/attributes/channels/_channels.py::Channels.can_freeze_selection(cls, attr_names)`
- `node_utils/attributes/channels/_channels.py::Channels.freeze_transforms(cls, nodes, attrs=None, store=True)`
- `node_utils/attributes/channels/_channels.py::Channels.has_unfreeze_info(nodes)`
- `node_utils/attributes/channels/_channels.py::Channels.unfreeze_transforms(cls, nodes, attrs=None)`

## Signature changed (1)

- `xform_utils/_xform_utils.py::XformUtils.restore_transforms`
  - was: `(objects, prefix='original', delete_attrs=True)`
  - now: `(objects, prefix='original', delete_attrs=True, channels=None)`
