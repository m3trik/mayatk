# !/usr/bin/python
# coding=utf-8
from typing import List, Union
import re
import string

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.xform_utils import XformUtils


class Naming(ptk.HelpMixin):
    """ """

    @classmethod
    @CoreUtils.undoable
    def rename(cls, objects, to, fltr="", regex=False, ignore_case=False):
        """Rename scene objects based on specified patterns and filters, ensuring compliance with Maya's naming conventions.

        Parameters:
            objects (str/obj/list): The object(s) to rename. If empty, all scene objects will be renamed.
            to (str): Desired name pattern. Asterisk (*) can be used for formatting:
                    chars - replace all.
                    *chars* - replace only.
                    *chars - replace suffix.
                    **chars - append suffix.
                    chars* - replace prefix.
                    chars** - append prefix.
            fltr (str): Filter to apply on object names using wildcards or regular expressions:
                    chars - exact match.
                    *chars* - contains chars.
                    *chars - ends with chars.
                    chars* - starts with chars.
                    chars|chars - matches any of the specified patterns.
            regex (bool): Use regular expressions if True, else use default '*' and '|' modifiers for pattern matching.
            ignore_case (bool): Ignore case when filtering. Applies only to the 'fltr' parameter.

        Returns:
            None: Objects are renamed in the scene directly.

        Example:
            rename('Cube', '*001', regex=True) # Replace suffix on objects containing 'Cube' in their name, e.g., 'polyCube' becomes 'polyCube001'.
            rename('Cube', '**001', regex=True) # Append '001' to names of objects containing 'Cube', e.g., 'polyCube1' becomes 'polyCube1001'.
        """
        objects = (
            pm.ls(objectsOnly=True, flatten=True)
            if not objects
            else pm.ls(objects, objectsOnly=True, flatten=True)
        )
        long_names = [obj.name() for obj in objects]
        short_names = [ii if ii else i for i, ii in ptk.split_at_chars(long_names)]

        names = ptk.find_str_and_format(
            short_names,
            to,
            fltr,
            regex=regex,
            ignore_case=ignore_case,
            return_orig_strings=True,
        )

        count = 0
        for oldName, newName in names:
            # Strip illegal characters from newName
            newName = cls.strip_illegal_chars(newName)

            # Ensure we map short names to their correct long names
            if oldName in short_names:
                index = short_names.index(oldName)
                oldName = long_names[index]
            else:
                print(
                    f"// Warning: '{oldName}' not found in the original short names list."
                )
                continue  # Skip renaming if the object was not in the original list

            try:
                if pm.objExists(oldName):
                    n = pm.rename(oldName, newName)  # Rename the object
                    if not n == newName:
                        pm.warning(
                            f"'{oldName}' renamed to: '{n}' instead of '{newName}'"
                        )
                    else:
                        print(f"'{oldName}' renamed to: '{newName}'")
                    count += 1
            except Exception as e:
                if not pm.ls(oldName, readOnly=True) == []:  # Ignore read-only errors
                    print(f"// Error: renaming '{oldName}' to '{newName}': {e}")

        print(f"// Result: Renamed {count} objects.")

    @classmethod
    def generate_unique_name(cls, base_name, suffix="_", padding=3):
        """Generate a unique name based on the base_name.

        Parameters:
            base_name (str): The base name to generate a unique name from.
            suffix (str): The suffix to append to the base_name. Default is underscore (_).
            padding (int): The number of digits to pad the suffix with. Default is 3.

        Returns:
            str: A unique name based on the base_name.

        Example:
            generate_unique_name("Cube") # Returns "Cube_001"
            generate_unique_name("Cube", suffix="-", padding=2) # Returns "Cube-01"
        """
        if not pm.objExists(base_name):
            return base_name

        counter = 1
        while True:
            new_name = f"{base_name}{suffix}{str(counter).zfill(padding)}"
            new_name_clean = cls.strip_illegal_chars(new_name)
            if new_name != new_name_clean:
                pm.warning(
                    f"// Warning: Illegal characters found in generated name: {new_name}, replacing with: {new_name_clean}"
                )
            if not pm.objExists(new_name_clean):
                return new_name_clean
            counter += 1

    @staticmethod
    def strip_illegal_chars(input_data, replace_with="_"):
        """Strips illegal characters from a string or a list of strings, replacing them with a specified character, conforming to Maya naming conventions.

        Parameters:
            input_data (str/list): A single string or a list of strings to be sanitized.
            replace_with (str): The character to replace illegal characters with. Default is underscore (_).

        Returns:
            str/list: Sanitized string or list of strings, with illegal characters replaced.
        """

        def clean_string(s):
            pattern = re.compile(r"[^a-zA-Z0-9_]")
            return pattern.sub(replace_with, s)

        if isinstance(input_data, (list, tuple, set)):
            return [clean_string(s) for s in input_data]
        elif isinstance(input_data, str):
            return clean_string(input_data)
        else:
            raise TypeError(
                "Input data must be a string or a list, tuple, set of strings."
            )

    @staticmethod
    @CoreUtils.undoable
    def strip_chars(
        objects: Union[str, object, List[Union[str, object]]],
        num_chars: int = 1,
        trailing: bool = False,
    ) -> List[str]:
        """Deletes leading or trailing characters from the names of the provided objects.

        Parameters:
            objects (Union[str, pm.PyNode, List[Union[str, pm.PyNode]]]): The input string, PyNode, or list of either.
            num_chars (int): The number of characters to delete.
            trailing (bool): Whether to delete characters from the rear of the name.
        """
        # Flatten the list of objects if needed
        objects = pm.ls(objects, flatten=True)
        for obj in objects:
            s = obj.shortName().split("|")[-1]
            if num_chars > len(s):
                pm.warning(
                    f'Cannot remove {num_chars} characters from "{s}" as it is shorter than {num_chars} characters.'
                )
                continue
            if trailing:
                new_name = s[:-num_chars]
            else:
                new_name = s[num_chars:]
            try:
                pm.rename(obj, new_name)
            except Exception as e:
                print(f"// Error: Unable to rename {s}: {e}")
                continue

    @staticmethod
    @CoreUtils.undoable
    def set_case(objects=[], case="caplitalize"):
        """Rename objects following the given case.

        Parameters:
            objects (str/list): The objects to rename. default:all scene objects
            case (str): Desired case using python case operators.
                    valid: 'upper', 'lower', 'caplitalize', 'swapcase' 'title'. default:'caplitalize'
        Example:
            set_case(pm.ls(sl=1), 'upper')
        """
        for obj in pm.ls(objects):
            name = obj.name()

            newName = getattr(name, case)()
            try:
                pm.rename(name, newName)
            except Exception as error:
                if not pm.ls(obj, readOnly=True) == []:  # Ignore read-only errors.
                    print(name + ": ", error)

    @staticmethod
    @CoreUtils.undoable
    def append_location_based_suffix(
        objects,
        first_obj_as_ref=False,
        alphabetical=False,
        strip_trailing_ints=True,
        strip_trailing_alpha=True,
        reverse=False,
    ):
        """Rename objects with a suffix defined by its location from origin.

        Parameters:
            objects (str)(int/list): The object(s) to rename.
            first_obj_as_ref (bool): When True, use the first object's bounding box center as reference_point instead of origin.
            alphabetical (str): When True use an alphabetical character as a suffix when there is less than 26 objects else use integers.
            strip_trailing_ints (bool): Strip any trailing integers. ie. 'cube123'
            strip_trailing_alpha (bool): Strip any trailing uppercase alphanumeric chars that are prefixed with an underscore.  ie. 'cube_A'
            reverse (bool): Reverse the naming order. (Farthest object first)
        """
        # Determine the reference point
        reference_point = [0, 0, 0]
        if first_obj_as_ref and objects:
            first_obj_bbox = pm.exactWorldBoundingBox(objects[0])
            reference_point = [
                (first_obj_bbox[i] + first_obj_bbox[i + 3]) / 2 for i in range(3)
            ]

        length = len(objects)
        if alphabetical:
            if length <= 26:
                suffix = string.ascii_uppercase
            else:
                suffix = [str(n).zfill(len(str(length))) for n in range(length)]
        else:
            suffix = [str(n).zfill(len(str(length))) for n in range(length)]

        ordered_objs = XformUtils.order_by_distance(
            objects, reference_point=reference_point, reverse=reverse
        )

        newNames = {}  # the object with the new name set as a key.
        for n, obj in enumerate(ordered_objs):
            current_name = obj.name()

            while (
                (current_name[-1] == "_" or current_name[-1].isdigit())
                and strip_trailing_ints
            ) or (
                (
                    len(current_name) > 1
                    and current_name[-2] == "_"
                    and current_name[-1].isupper()
                )
                and strip_trailing_alpha
            ):
                if (
                    current_name[-2] == "_" and current_name[-1].isupper()
                ) and strip_trailing_alpha:  # trailing underscore and uppercase alphanumeric char.
                    current_name = re.sub(
                        re.escape(current_name[-2:]) + "$", "", current_name
                    )

                if (
                    current_name[-1] == "_" or current_name[-1].isdigit()
                ) and strip_trailing_ints:  # trailing underscore and integers.
                    current_name = re.sub(
                        re.escape(current_name[-1:]) + "$", "", current_name
                    )

            obj_suffix = suffix[n]
            newNames[obj] = current_name + "_" + obj_suffix

        # Rename all with a placeholder first so that there are no conflicts.
        for obj in ordered_objs:
            pm.rename(obj, "p0000000000")
        for obj in ordered_objs:  # Rename all with the new names.
            pm.rename(obj, newNames[obj])


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
