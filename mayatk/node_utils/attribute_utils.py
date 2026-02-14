# !/usr/bin/python
# coding=utf-8
from typing import Any, List, Optional, Union

try:
    import pymel.core as pm
except ImportError:
    pass

import pythontk as ptk


class AttributeTemplate:
    """Defines the configuration for a Maya attribute."""

    def __init__(
        self,
        long_name: str,
        attribute_type: str = "float",
        keyable: bool = True,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        default_value: Optional[Any] = None,
        enum_names: Optional[List[str]] = None,
    ):
        self.long_name = long_name
        self.attribute_type = attribute_type
        self.keyable = keyable
        self.min_value = min_value
        self.max_value = max_value
        self.default_value = default_value
        self.enum_names = enum_names

    def __repr__(self):
        return f"<AttributeTemplate '{self.long_name}' " f"({self.attribute_type})>"


class AttributeManager(ptk.HelpMixin):
    """
    Generic utility for managing Maya attributes.
    Supports template-based creation.
    """

    Template = AttributeTemplate

    @classmethod
    def create_attributes(cls, objects, template: AttributeTemplate) -> List[str]:
        """Apply an AttributeTemplate to a list of objects."""
        added = []
        for obj in pm.ls(objects):
            if cls.ensure_attribute(obj, template):
                added.append(f"{obj.name()}.{template.long_name}")
        return added

    @classmethod
    def ensure_attribute(cls, obj, template: AttributeTemplate) -> bool:
        """Create an attribute on obj defined by template if it doesn't exist."""
        if obj.hasAttr(template.long_name):
            return True

        kwargs = {
            "longName": template.long_name,
            "keyable": template.keyable,
        }

        # Type handling (Maya specific quirks)
        if template.attribute_type in ["string", "stringArray"]:
            kwargs["dataType"] = template.attribute_type
        elif template.attribute_type == "enum":
            kwargs["attributeType"] = "enum"
            if template.enum_names:
                kwargs["enumName"] = ":".join(template.enum_names)
        else:
            kwargs["attributeType"] = template.attribute_type

        # Limits
        if template.min_value is not None:
            kwargs["minValue"] = template.min_value
        if template.max_value is not None:
            kwargs["maxValue"] = template.max_value
        if template.default_value is not None:
            kwargs["defaultValue"] = template.default_value

        try:
            # Use pm.addAttr instead of obj.addAttr to avoid PyMEL DependNode method signature conflicts
            pm.addAttr(obj, **kwargs)
            attr = obj.attr(template.long_name)

            # Keyable/Displayable state
            if template.keyable:
                attr.setKeyable(True)
            else:
                attr.showInChannelBox(True)

            # Set default value if provided (addAttr doesn't always set current value to default)
            if template.default_value is not None:
                attr.set(template.default_value)

            return True

        except Exception as e:
            print(
                f"[{cls.__name__}] Failed to add attribute {template.long_name} to {obj}: {e}"
            )
            return False
