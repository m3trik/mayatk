# !/usr/bin/python
# coding=utf-8
from typing import List, Union, Optional, Dict
import re
import string

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils


class Naming(ptk.HelpMixin):
    """ """

    @classmethod
    @CoreUtils.undoable
    def rename(
        cls,
        objects: Union[
            str, "pm.nodetypes.Transform", List[Union[str, "pm.nodetypes.Transform"]]
        ],
        to: str,
        fltr: str = "",
        regex: bool = False,
        ignore_case: bool = False,
        retain_suffix: bool = False,
        valid_suffixes: Optional[List[str]] = None,
    ) -> None:
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
                    chars - exact match (e.g., 'Cube' matches only 'Cube').
                    *chars* - contains chars (e.g., '*Cube*' matches 'pCube1', 'nurbsCube', etc.).
                    *chars - ends with chars (e.g., '*Cube' matches 'polyCube', 'nurbsCube').
                    chars* - starts with chars (e.g., 'Cube*' matches 'Cube1', 'CubeGroup').
                    chars|chars - matches any of the specified patterns (e.g., 'Cube|Sphere').
                    "" (empty) - matches all objects when used with formatting patterns.
            regex (bool): Use regular expressions if True, else use default '*' and '|' modifiers for pattern matching.
            ignore_case (bool): Ignore case when filtering. Applies only to the 'fltr' parameter.
            retain_suffix (bool): If True, append the original object's suffix (e.g., _GEO) to the new name unless already present.
            valid_suffixes (Optional[List[str]]): List of valid suffixes to retain. If provided, only these suffixes will be retained.
                If None, any suffix (text after last underscore) will be retained. Default is None.

        Returns:
            None: Objects are renamed in the scene directly.

        Example:
            rename(['pCube1'], '*001', '*Cube*') # Matches objects containing 'Cube', replaces suffix: 'pCube1' becomes 'pCube001'.
            rename(['pCube1'], '**001', '*Cube*') # Matches objects containing 'Cube', appends suffix: 'pCube1' becomes 'pCube1001'.
            rename(['polyCube'], 'newName', 'Cube') # Exact match required: 'polyCube' won't match, 'Cube' would match.
            rename(['pCube1'], '*GEO', retain_suffix=True) # Appends the original suffix (e.g. _GEO) to the new name.
        """
        objects = pm.ls(objects, flatten=True)

        # Create a mapping of short names to their PyMEL objects (not cached long names)
        # This prevents issues when renaming changes hierarchy paths
        short_name_to_obj = {}
        for obj in objects:
            long_name = obj.name()
            _, short_name = ptk.split_delimited_string(long_name, occurrence=-1)
            short_name = short_name if short_name else long_name
            short_name_to_obj[short_name] = obj

        short_names = list(short_name_to_obj.keys())

        # Handle empty filter case which causes crashes
        if not fltr:
            # When no filter is provided, apply formatting to all objects
            names = []
            for name in short_names:
                try:
                    formatted = ptk.find_str_and_format(
                        [name],
                        to,
                        "*",
                        regex=regex,
                        ignore_case=ignore_case,
                        return_orig_strings=True,
                    )
                    if formatted:
                        names.extend(formatted)
                except Exception:
                    # Fallback: simple append for basic patterns
                    if to.startswith("**"):
                        new_name = name + to[2:]
                    elif to.startswith("*"):
                        new_name = name + to[1:]
                    else:
                        new_name = to
                    names.append((name, new_name))
        else:
            try:
                names = ptk.find_str_and_format(
                    short_names,
                    to,
                    fltr,
                    regex=regex,
                    ignore_case=ignore_case,
                    return_orig_strings=True,
                )
            except Exception as e:
                print(f"// Error in find_str_and_format: {e}")
                print(f"// Filter: '{fltr}', Pattern: '{to}'")
                print(
                    f"// Try using wildcard patterns like '*{fltr}*' for partial matches"
                )
                return

        count = 0
        for oldName, newName in names:
            # Optionally retain suffix from oldName
            if retain_suffix:
                # Suffix is defined as everything after the last underscore, including the underscore
                suffix = ""
                if "_" in oldName:
                    suffix = oldName[oldName.rfind("_") :]
                    # If valid_suffixes is provided, only retain if suffix is in the list
                    if valid_suffixes is not None and suffix not in valid_suffixes:
                        suffix = ""
                    # Avoid duplicate suffix
                    if suffix and not newName.endswith(suffix):
                        newName += suffix

            # Strip illegal characters from newName
            newName = cls.strip_illegal_chars(newName)

            # Map short name to the PyMEL object for renaming
            # Using the object reference instead of cached paths prevents issues
            # when earlier renames in the batch change the hierarchy
            if oldName in short_name_to_obj:
                obj = short_name_to_obj[oldName]
                try:
                    n = pm.rename(obj, newName)  # Rename using the object reference
                    if not n == newName:
                        pm.warning(
                            f"'{oldName}' renamed to: '{n}' instead of '{newName}'"
                        )
                    else:
                        print(f"'{oldName}' renamed to: '{newName}'")
                    count += 1
                except Exception as e:
                    if not pm.ls(obj, readOnly=True) == []:  # Ignore read-only errors
                        print(f"// Error: renaming '{oldName}' to '{newName}': {e}")
            else:
                print(
                    f"// Warning: '{oldName}' not found in the original short names list."
                )
                continue  # Skip renaming if the object was not in the original list

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
        """Deletes leading or trailing characters from the names of the provided objects,
        ensuring legality in Maya names.

        Parameters:
            objects (Union[str, pm.PyNode, List[Union[str, pm.PyNode]]]): Input objects.
            num_chars (int): Number of characters to delete.
            trailing (bool): If True, delete from end, else from start.

        Returns:
            List[str]: New names assigned.
        """
        objects = pm.ls(objects, flatten=True)
        name_pairs = []
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
                temp_name = s[num_chars:]
                # Maya does not allow names starting with a digit
                if temp_name and temp_name[0].isdigit():
                    temp_name = "_" + temp_name[1:]
                new_name = temp_name

            # Ensure name is not empty and legal
            if not new_name or not (new_name[0].isalpha() or new_name[0] == "_"):
                pm.warning(
                    f'Name "{new_name}" is not a legal Maya identifier, skipping.'
                )
                continue

            name_pairs.append((obj, new_name))

        for obj, new_name in name_pairs:
            try:
                pm.rename(obj, new_name)
            except Exception as e:
                print(f"// Error: Unable to rename {obj}: {e}")
                continue
        return [n for _, n in name_pairs]

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
    def suffix_by_type(
        objects: Union[str, object, List[Union[str, object]]],
        group_suffix: str = "_GRP",
        locator_suffix: str = "_LOC",
        joint_suffix: str = "_JNT",
        mesh_suffix: str = "_GEO",
        nurbs_curve_suffix: str = "_CRV",
        camera_suffix: str = "_CAM",
        light_suffix: str = "_LGT",
        display_layer_suffix: str = "_LYR",
        custom_suffixes: Optional[Dict[str, str]] = None,
        strip: Union[str, List[str]] = None,
        strip_trailing_ints: bool = False,
    ) -> List[str]:
        """Appends a conventional suffix based on Maya object type, stripping any existing known suffix.

        Parameters:
            objects: Objects to rename.
            group_suffix (str): Suffix for transform groups.
            locator_suffix (str): Suffix for locators.
            joint_suffix (str): Suffix for joints.
            mesh_suffix (str): Suffix for meshes.
            nurbs_curve_suffix (str): Suffix for nurbs curves.
            camera_suffix (str): Suffix for cameras.
            light_suffix (str): Suffix for lights.
            display_layer_suffix (str): Suffix for display layers.
            custom_suffixes (dict): Mapping of Maya node type to suffix.
            strip (str or list): Extra suffix(es) to strip from the end of the name before applying the new suffix.
            strip_trailing_ints (bool): If True, remove all trailing integers after stripping suffixes.

        Returns:
            List[str]: List of new names assigned.
        """
        default_map = {
            "group": group_suffix,
            "locator": locator_suffix,
            "joint": joint_suffix,
            "mesh": mesh_suffix,
            "nurbsCurve": nurbs_curve_suffix,
            "camera": camera_suffix,
            "light": light_suffix,
            "displayLayer": display_layer_suffix,
        }
        if custom_suffixes:
            default_map.update(custom_suffixes)

        # Get all suffixes for potential stripping
        all_suffixes = set(default_map.values())
        if strip:
            all_suffixes.update(ptk.make_iterable(strip))

        objects = pm.ls(objects, flatten=True)
        name_pairs = []

        for obj in objects:
            short_name = obj.shortName().split("|")[-1]
            # Use NodeUtils for object type resolution
            typ = NodeUtils.get_type(obj)
            target_suffix = default_map.get(typ, "")
            if not target_suffix:
                # fallback to nodeType-based detection if needed
                node_type = obj.nodeType()
                target_suffix = default_map.get(node_type, "")

            # Strip wrong suffixes from the END of the name only
            wrong_suffixes = [s for s in all_suffixes if s != target_suffix]
            base_name = short_name
            for wrong_suffix in wrong_suffixes:
                if base_name.endswith(wrong_suffix):
                    base_name = base_name[: -len(wrong_suffix)]
                    break  # Only strip one suffix to avoid over-stripping

            # Apply strip_trailing_ints if specified
            if strip_trailing_ints:
                base_name = ptk.format_suffix(
                    base_name,
                    suffix="",
                    strip_trailing_ints=True,
                    strip_trailing_alpha=False,
                )

            # Only add target suffix if not already present
            if target_suffix and not base_name.endswith(target_suffix):
                new_name = base_name + target_suffix
            else:
                new_name = base_name

            name_pairs.append((obj, new_name))

        for obj, new_name in name_pairs:
            try:
                pm.rename(obj, new_name)
            except Exception as e:
                print(f"// Error: Unable to rename {obj}: {e}")
                continue

        return [n for _, n in name_pairs]

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
