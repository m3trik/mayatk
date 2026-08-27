# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from typing import List, Union, Optional, Dict, Tuple
import re
import string

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils


class Naming(ptk.HelpMixin, ptk.LoggingMixin):
    """Batch find / rename / suffix scene nodes.

    Every renaming operation plans its new names first and hands the plan to
    :class:`pythontk.RenamePlan`, which applies it (or, with ``dry_run=True``,
    only reports it) and emits one report record per operation through
    ``cls.logger`` — so a tool that redirects this logger into a panel gets the
    ``old → new`` listing for free, and a script gets it on the console.
    """

    # Suffix-by-type bindings: (keyword, NamingConvention key, Maya type key).
    # LITERAL and complete on purpose -- this is the one thing the shared
    # convention cannot know: which of THIS host's node types each entry names.
    # The affix spelling and the display label are deliberately absent; they
    # come from :class:`pythontk.NamingConvention`, so editing the convention in
    # the Naming panel moves every tool at once instead of only this one.
    # The Maya type key is what :meth:`type_key` resolves a node to;
    # ``custom_suffixes`` may add any further Maya node type.
    SUFFIX_BINDINGS: Tuple[Tuple[str, str, str], ...] = (
        ("group_suffix", "group", "group"),
        ("locator_suffix", "locator", "locator"),
        ("joint_suffix", "joint", "joint"),
        ("mesh_suffix", "mesh", "mesh"),
        ("nurbs_curve_suffix", "nurbsCurve", "nurbsCurve"),
        ("camera_suffix", "camera", "camera"),
        ("light_suffix", "light", "light"),
        ("display_layer_suffix", "displayLayer", "displayLayer"),
        ("ik_handle_suffix", "ikHandle", "ikHandle"),
        ("nurbs_surface_suffix", "nurbsSurface", "nurbsSurface"),
        ("cluster_suffix", "cluster", "cluster"),
        ("lattice_suffix", "lattice", "lattice"),
        ("skin_cluster_suffix", "skinCluster", "skinCluster"),
        ("blend_shape_suffix", "blendShape", "blendShape"),
        ("constraint_suffix", "constraint", "constraint"),
        ("material_suffix", "material", "material"),
        ("shading_group_suffix", "shadingEngine", "shadingEngine"),
        ("texture_suffix", "texture", "texture"),
        ("set_suffix", "objectSet", "objectSet"),
    )

    @ptk.ClassProperty
    def SUFFIX_TYPES(cls) -> Tuple[Tuple[str, str, str, str], ...]:
        """``(keyword, affix, label, type key)`` -- the live convention, joined
        to this host's type bindings.

        A computed view, not a stored table: the affix and label columns are
        read from :class:`pythontk.NamingConvention` on every access, so a panel
        that rebuilds its fields shows what the user actually set rather than a
        default frozen at import. Kept in the original four-column shape because
        it is the published surface; :attr:`SUFFIX_BINDINGS` is the source.
        """
        return tuple(
            (kw, ptk.NamingConvention.affix(ck), ptk.NamingConvention.label(ck), tk)
            for kw, ck, tk in cls.SUFFIX_BINDINGS
        )

    @classmethod
    def affix_rules(
        cls,
        overrides: Optional[Dict[str, str]] = None,
        modes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, "ptk.AffixRule"]:
        """``{Maya type key: AffixRule}`` -- the convention bound to this host.

        The join that every type-driven rename runs on. Each entry starts as the
        shared convention's rule and is then overridden per call, so a tool can
        deviate for one run without editing (or being surprised by) the SSoT.

        Parameters:
            overrides: ``{engine keyword or type key: affix spelling}``. An
                empty string disables that type. Accepts either key form so a
                caller can pass ``suffix_by_type``'s keywords straight through.
            modes: ``{engine keyword or type key: "auto"/"suffix"/"prefix"}`` --
                placement overrides for the same entries.

        Returns:
            One rule per bound type key, ready to :py:meth:`AffixRule.apply`.
        """
        return ptk.NamingConvention.bind(cls.SUFFIX_BINDINGS, overrides, modes)

    @classmethod
    @CoreUtils.undoable
    def rename(
        cls,
        objects: Union[str, "object", List[Union[str, "object"]]],
        to: str,
        fltr: str = "",
        regex: bool = False,
        ignore_case: bool = False,
        retain_suffix: bool = False,
        valid_suffixes: Optional[List[str]] = None,
        collapse_padding: bool = True,
        dry_run: bool = False,
    ) -> List[str]:
        """Rename scene objects based on specified patterns and filters, ensuring compliance with Maya's naming conventions.

        Parameters:
            objects (str/obj/list): The object(s) to rename. If empty, all scene objects will be renamed.
            to (str): Desired name pattern. The asterisk (*) marks the part of the existing
                    name that is KEPT, so one asterisk replaces that side and a doubled one
                    keeps the whole name and adds to it:
                    chars - replace all.
                    *chars* - replace only the part matched by 'fltr'.
                    *chars - replace suffix (drops from the match onward).
                    **chars - append suffix (keeps the whole name).
                    chars* - replace prefix (drops through the match).
                    chars** - append prefix (keeps the whole name).
                    "" (empty) - strip the part matched by 'fltr'.
                    Pipe-separated terms pair positionally with 'fltr''s terms, so
                    fltr '*_L|*_R' with to '*_lt|*_rt' renames each side differently;
                    a single term applies to every filter term. Replace-prefix and
                    replace-suffix fall back to appending when 'fltr' is empty or its
                    text is absent from a name.
            fltr (str): Filter to apply on object names using wildcards or regular expressions:
                    chars - exact match (e.g., 'Cube' matches only 'Cube').
                    *chars* - contains chars (e.g., '*Cube*' matches 'pCube1', 'nurbsCube', etc.).
                    *chars - ends with chars (e.g., '*Cube' matches 'polyCube', 'nurbsCube').
                    chars* - starts with chars (e.g., 'Cube*' matches 'Cube1', 'CubeGroup').
                    chars|chars - matches any of the specified patterns (e.g., 'Cube|Sphere').
                        Each term also supplies the "from" text for the names it matched.
                    "" (empty) - matches all objects when used with formatting patterns.
            regex (bool): Use regular expressions if True, else use default '*' and '|' modifiers for pattern matching.
                    The pattern drives the substitution as well as the search, and its capture
                    groups are available in 'to' as '\\1', '\\2' or '\\g<name>'. In regex mode
                    '|' stays alternation rather than a term separator.
            ignore_case (bool): Ignore case when filtering. Applies to the 'fltr' parameter
                    and to the substitution it drives.
            retain_suffix (bool): Carry the original object's type suffix (e.g. _GEO)
                over to the new name when the pattern loses it. A name with no
                defined suffix is left alone, and a new name that still carries
                the suffix is never given a second copy.
            valid_suffixes (Optional[List[str]]): The suffixes that count as type
                suffixes. None (the default) uses the shared naming convention's
                affixes, so only a *defined* suffix is ever retained.
            collapse_padding (bool): Collapse runs of 2+ underscores in the result and strip
                trailing ones — the separator residue strip/replace formatting leaves behind
                (removing a token from 'a__tok__tokB' yields 'a____B' -> 'a_B'). Skipped
                automatically when the 'to' pattern itself contains '__'. Pass False to
                preserve every underscore run in names the operation touches.
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            list[str]: The new names of the renamed objects (parallel to ``objects``).
                Returns the original name for any object that could not be renamed;
                on a dry run, the planned leaf name for each object that would change.

        Example:
            rename(['pCube1'], '*001', '*Cube*') # Matches objects containing 'Cube', replaces suffix: 'pCube1' becomes 'pCube001'.
            rename(['pCube1'], '**001', '*Cube*') # Matches objects containing 'Cube', appends suffix: 'pCube1' becomes 'pCube1001'.
            rename(['polyCube'], 'newName', 'Cube') # Exact match required: 'polyCube' won't match, 'Cube' would match.
            rename(['pCube1'], '*GEO', retain_suffix=True) # Appends the original suffix (e.g. _GEO) to the new name.
            rename(['arm_L','arm_R'], '*_lt|*_rt', '*_L|*_R') # Paired terms: 'arm_L' becomes 'arm_lt', 'arm_R' becomes 'arm_rt'.
            rename(['pCube1'], r'*\\1_GEO', r'Cube(\\d+)', regex=True) # Backref: 'pCube1' becomes 'p1_GEO'.
        """
        objects = cmds.ls(CoreUtils.as_strings(objects), flatten=True, long=True)

        # Map each short name to a LIST of (original_long_path, uuid) pairs.
        # The short-name key must match ``find_str_and_format``'s oldName output
        # (which operates on short names); the UUID makes the batch rename immune
        # to both duplicate leaf names AND intra-batch hierarchy changes (an
        # earlier rename can invalidate a cached long path, so the object is
        # re-resolved from its UUID at rename time).
        short_name_to_objs = {}
        short_names = []
        obj_keys = []
        for obj in objects:
            short_name = cls._leaf(obj)
            key = cls._key(obj)
            short_name_to_objs.setdefault(short_name, []).append(key)
            short_names.append(short_name)
            obj_keys.append(key)

        # One batch call covers both cases: an empty filter means "match all",
        # and duplicate short names survive (the formatter no longer dedupes),
        # so the per-name loop this used to need is gone. Mirrors blendertk.
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
            cls.logger.error(
                f"Invalid pattern — filter '{fltr}', rename '{to}': {e}. "
                f"Try a wildcard such as '*{fltr}*' for partial matches."
            )
            return list(objects)

        # "Retain defined suffix": with no explicit list the shared naming
        # convention decides what counts as a type suffix. Unrestricted
        # retention treats ANY trailing token as one ('wall_low' -> 'Prop_low'),
        # which reads as adding a suffix to a name that never had one.
        if retain_suffix and valid_suffixes is None:
            valid_suffixes = ptk.NamingConvention.all_affixes()

        plan = []
        for oldName, newName in names:
            if retain_suffix:
                newName = ptk.retain_suffix(oldName, newName, valid_suffixes)

            # Strip illegal characters from newName
            newName = cls.strip_illegal_chars(newName)

            # Collapse the separator residue that strip/replace formatting
            # leaves behind (removing a token from 'a__tok__tokB' yields
            # 'a____B'). An explicit '__' typed in the pattern is honored.
            # Mirrors blendertk's Naming.rename.
            if collapse_padding and "__" not in to:
                collapsed = ptk.collapse_delimiter_runs(newName)
                if collapsed:
                    newName = collapsed

            bucket = short_name_to_objs.get(oldName)
            if not bucket:
                cls.logger.warning(
                    f"'{oldName}' not found in the original short names list."
                )
                continue
            plan.append((bucket.pop(0), oldName, newName))

        if not plan and objects:
            cls.logger.warning(f"No objects matched '{fltr}'.")
            return list(objects)

        title = f"Rename{f' — matching {fltr!r}' if fltr else ''}"
        finals = cls._apply_plan(plan, title, dry_run)
        renamed = {key: name for (key, _o, _n), name in zip(plan, finals)}
        return [renamed.get(key, obj) for key, obj in zip(obj_keys, objects)]

    @classmethod
    def scene_objects(cls) -> List[str]:
        """Every renameable node in the scene — the naming tools' "Scene" scope.

        Excludes Maya's built-ins (default, undeletable — the startup cameras
        and managers — and read-only nodes) and shapes (a shape follows its
        transform's name; renaming both would double-suffix).

        Returns:
            list[str]: Long names.
        """
        skip = set(cmds.ls(defaultNodes=True, long=True) or [])
        skip.update(cmds.ls(undeletable=True, long=True) or [])
        skip.update(cmds.ls(readOnly=True, long=True) or [])
        skip.update(cmds.ls(shapes=True, long=True) or [])
        return [n for n in cmds.ls(long=True) or [] if n not in skip]

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
        if not cmds.objExists(base_name):
            return base_name

        counter = 1
        while True:
            new_name = f"{base_name}{suffix}{str(counter).zfill(padding)}"
            new_name_clean = cls.strip_illegal_chars(new_name)
            if new_name != new_name_clean:
                cmds.warning(
                    f"// Warning: Illegal characters found in generated name: {new_name}, replacing with: {new_name_clean}"
                )
            if not cmds.objExists(new_name_clean):
                return new_name_clean
            counter += 1

    @classmethod
    @CoreUtils.undoable
    def conform_shape_names(
        cls,
        objects: Union[str, "object", List[Union[str, "object"]], None] = None,
        force: bool = False,
    ) -> List[Tuple[str, str]]:
        """Rename shape nodes to Maya's conventional ``<transform>Shape`` form.

        Maya only auto-renames a shape alongside its transform when the
        shape is uniquely parented and already follows the convention — a
        shared (instanced) shape, or one carrying an imported/scratch name,
        keeps its stale name forever and spreads it to every instance path.
        This conforms each shape to ``<parentBase>Shape<parentDigits>``
        (Maya's own spelling: ``vdat1`` → ``vdatShape1``), with ``Orig``
        appended for intermediate shapes.  Instanced shapes are renamed
        once, via their first instance parent.

        Parameters:
            objects: Transforms (or shapes) to conform.  None conforms
                every shape in the scene.
            force: Also re-derive names that already conform (e.g. after
                re-parenting under a differently named transform).

        Returns:
            list[tuple[str, str]]: ``(old_leaf, new_leaf)`` per rename.
        """
        if objects is None:
            shapes = cmds.ls(shapes=True, long=True) or []
        else:
            objs = cmds.ls(CoreUtils.as_strings(objects), long=True) or []
            if not objs:  # listRelatives([]) would fall back to the selection
                return []
            shapes = [o for o in objs if cmds.ls(o, shapes=True)]
            shapes += (
                cmds.listRelatives(
                    objs, allDescendents=True, fullPath=True, type="shape"
                )
                or []
            )

        pairs = []
        seen = set()
        for shape in shapes:
            uuid = (cmds.ls(shape, uuid=True) or [None])[0]
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)
            # Re-resolve — an earlier rename in this batch never changes an
            # ancestor path (only leaves are renamed), but resolving via the
            # UUID keeps the path authoritative regardless.
            path = (cmds.ls(uuid, long=True) or [shape])[0]
            leaf = path.split("|")[-1].split(":")[-1]
            parents = cmds.listRelatives(path, allParents=True, fullPath=True) or []
            if not parents:
                continue
            parent_leaf = parents[0].split("|")[-1].split(":")[-1]
            base = re.sub(r"\d+$", "", parent_leaf)
            digits = parent_leaf[len(base) :]
            want = f"{base}Shape{digits}" + (
                "Orig" if NodeUtils.is_intermediate(path) else ""
            )
            # Already conforming (allowing Maya's clash-digit suffix)?
            if not force and re.fullmatch(
                re.escape(base) + r"Shape\d*(Orig)?\d*", leaf
            ):
                continue
            try:
                renamed = cmds.rename(path, want)
                pairs.append((leaf, str(renamed).split("|")[-1]))
            except RuntimeError as e:
                cmds.warning(f"Could not conform shape '{leaf}': {e}")
        return pairs

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

    @classmethod
    @CoreUtils.undoable
    def strip_chars(
        cls,
        objects: Union[str, object, List[Union[str, object]]],
        num_chars: int = 1,
        trailing: bool = False,
        dry_run: bool = False,
    ) -> List[str]:
        """Deletes leading or trailing characters from the names of the provided objects,
        ensuring legality in Maya names.

        Parameters:
            objects (Union[str, List[str]]): Input objects.
            num_chars (int): Number of characters to delete.
            trailing (bool): If True, delete from end, else from start.
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            List[str]: New names assigned (one per object that could be renamed).
        """
        objects = cmds.ls(objects, flatten=True, long=True)
        plan = []
        for obj in objects:
            s = cls._leaf(obj)
            if num_chars > len(s):
                cls.logger.warning(
                    f"Skipped '{s}': cannot remove {num_chars} characters from a "
                    f"{len(s)}-character name."
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
                cls.logger.warning(
                    f"Skipped '{s}': '{new_name}' is not a legal Maya identifier."
                )
                continue

            plan.append((cls._key(obj), s, new_name))

        return cls._apply_plan(plan, "Strip Chars", dry_run)

    @classmethod
    @CoreUtils.undoable
    def set_case(cls, objects=None, case="capitalize", dry_run: bool = False):
        """Rename objects following the given case.

        Parameters:
            objects (str/list): The objects to rename. default:all scene objects
            case (str): Desired case using python case operators.
                    valid: 'upper', 'lower', 'capitalize', 'swapcase', 'title'. default:'capitalize'
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            List[str]: The names after the operation, parallel to the objects.

        Example:
            set_case(cmds.ls(sl=1), 'upper')
        """
        objects = cmds.ls(objects, long=True) if objects else cmds.ls(long=True)
        plan = []
        for obj in objects:
            leaf = cls._leaf(obj)
            plan.append((cls._key(obj), leaf, ptk.set_case(leaf, case)))
        return cls._apply_plan(plan, f"Convert Case ({case})", dry_run)

    @classmethod
    def type_key(cls, obj: str) -> str:
        """Resolve a node to its suffix-by-type key (see ``SUFFIX_TYPES``).

        A transform resolves through its first non-intermediate shape, so a
        camera / curve / light *transform* classifies like its shape; a
        shapeless plain transform is a ``group``. Lights are detected by
        inheritance (Arnold / renderer lights included), materials and
        textures by Maya's own ``ls -materials`` / ``ls -textures``
        classification. Anything else returns its Maya node type, which is
        what a ``custom_suffixes`` mapping keys on.

        Returns:
            str: One of the ``SUFFIX_TYPES`` keys, or the raw node type.
        """
        node = str(obj)
        node_type = cmds.objectType(node)
        inherited = cmds.nodeType(node, inherited=True) or []
        if "dagNode" in inherited and "shape" not in inherited:
            shapes = (
                cmds.listRelatives(
                    node, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            if shapes:
                node = shapes[0]
                node_type = cmds.objectType(node)
                inherited = cmds.nodeType(node, inherited=True) or []
            elif node_type == "transform":
                return "group"
            elif "constraint" in inherited:
                return "constraint"
            else:
                return node_type  # joint, ikHandle, ...

        if node_type in ("clusterHandle", "cluster"):
            return "cluster"
        if node_type in ("lattice", "baseLattice", "ffd"):
            return "lattice"
        if "nurbsCurve" in inherited:  # bezierCurve too
            return "nurbsCurve"
        if "light" in inherited:
            return "light"
        if "constraint" in inherited:
            return "constraint"
        if node_type in (
            "locator",
            "mesh",
            "nurbsSurface",
            "camera",
            "skinCluster",
            "blendShape",
            "displayLayer",
            "shadingEngine",
            "objectSet",
        ):
            return node_type
        if cmds.ls(node, materials=True):
            return "material"
        if cmds.ls(node, textures=True):
            return "texture"
        return node_type

    @classmethod
    @CoreUtils.undoable
    def suffix_by_type(
        cls,
        objects: Union[str, object, List[Union[str, object]]],
        group_suffix: Optional[str] = None,
        locator_suffix: Optional[str] = None,
        joint_suffix: Optional[str] = None,
        mesh_suffix: Optional[str] = None,
        nurbs_curve_suffix: Optional[str] = None,
        camera_suffix: Optional[str] = None,
        light_suffix: Optional[str] = None,
        display_layer_suffix: Optional[str] = None,
        ik_handle_suffix: Optional[str] = None,
        nurbs_surface_suffix: Optional[str] = None,
        cluster_suffix: Optional[str] = None,
        lattice_suffix: Optional[str] = None,
        skin_cluster_suffix: Optional[str] = None,
        blend_shape_suffix: Optional[str] = None,
        constraint_suffix: Optional[str] = None,
        material_suffix: Optional[str] = None,
        shading_group_suffix: Optional[str] = None,
        texture_suffix: Optional[str] = None,
        set_suffix: Optional[str] = None,
        custom_suffixes: Optional[Dict[str, str]] = None,
        affix_mode: Optional[str] = None,
        affix_modes: Optional[Dict[str, str]] = None,
        strip: Union[str, List[str]] = None,
        strip_trailing_ints: bool = False,
        strip_trailing_underscores: bool = False,
        strip_trailing_padding: bool = True,
        dry_run: bool = False,
    ) -> List[str]:
        """Apply each object's conventional type **affix**, stripping any other it carries.

        An affix, not merely a suffix: every entry declares a spelling *and* the
        side of the name it lands on, so a studio that writes ``GEO_body``
        rather than ``body_GEO`` is a convention change, not a code change. A
        node's type is resolved by :meth:`type_key` (a transform through its
        shape). An empty affix disables that type -- the node keeps its base
        name, with any other convention affix stripped off.

        Each ``*_suffix`` keyword defaults to ``None``, meaning "whatever
        :class:`pythontk.NamingConvention` says" -- the single definition the
        whole toolset shares and the Naming panel edits. Pass a string to
        deviate for this one call.

        Parameters:
            objects: Objects to rename.
            group_suffix (str): Affix for transform groups (shapeless transforms).
            locator_suffix (str): Affix for locators.
            joint_suffix (str): Affix for joints.
            mesh_suffix (str): Affix for meshes.
            nurbs_curve_suffix (str): Affix for nurbs (and bezier) curves.
            camera_suffix (str): Affix for cameras.
            light_suffix (str): Affix for lights (any node inheriting ``light``).
            display_layer_suffix (str): Affix for display layers.
            ik_handle_suffix (str): Affix for IK handles.
            nurbs_surface_suffix (str): Affix for nurbs surfaces.
            cluster_suffix (str): Affix for cluster deformers and their handles.
            lattice_suffix (str): Affix for lattice (ffd) deformers, lattices and base lattices.
            skin_cluster_suffix (str): Affix for skin clusters.
            blend_shape_suffix (str): Affix for blend shapes.
            constraint_suffix (str): Affix for constraints (any type).
            material_suffix (str): Affix for materials (``ls -materials``).
            shading_group_suffix (str): Affix for shading groups.
            texture_suffix (str): Affix for texture nodes (``ls -textures``).
            set_suffix (str): Affix for object sets (shading groups excluded).
            custom_suffixes (dict): Mapping of Maya node type to affix; overrides the above.
            affix_mode (str): Placement for EVERY type -- ``"auto"`` (infer from
                the delimiter: ``"_GEO"`` trails, ``"GEO_"`` leads), ``"suffix"``
                or ``"prefix"``. ``None`` (default) keeps each entry's own mode
                from the convention.
            affix_modes (dict): Per-type placement, keyed by engine keyword or
                Maya type key. Wins over *affix_mode*.
            strip (str or list): Extra affix(es) to strip from the name before applying
                the new one. Matched at either end, like the convention's own.
            strip_trailing_ints (bool): If True, remove all trailing integers after stripping affixes.
            strip_trailing_underscores (bool): If True, remove trailing underscores after stripping.
            strip_trailing_padding (bool): If True, strip orphaned trailing underscores and,
                only when underscores were actually at the end, also strip the now-exposed
                trailing digits.  This preserves intentional ``_02`` numbering while cleaning
                up artifacts left by affix removal (e.g. ``Foo_`` -> ``Foo``).
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            List[str]: List of new names assigned.
        """
        given = {
            "group_suffix": group_suffix,
            "locator_suffix": locator_suffix,
            "joint_suffix": joint_suffix,
            "mesh_suffix": mesh_suffix,
            "nurbs_curve_suffix": nurbs_curve_suffix,
            "camera_suffix": camera_suffix,
            "light_suffix": light_suffix,
            "display_layer_suffix": display_layer_suffix,
            "ik_handle_suffix": ik_handle_suffix,
            "nurbs_surface_suffix": nurbs_surface_suffix,
            "cluster_suffix": cluster_suffix,
            "lattice_suffix": lattice_suffix,
            "skin_cluster_suffix": skin_cluster_suffix,
            "blend_shape_suffix": blend_shape_suffix,
            "constraint_suffix": constraint_suffix,
            "material_suffix": material_suffix,
            "shading_group_suffix": shading_group_suffix,
            "texture_suffix": texture_suffix,
            "set_suffix": set_suffix,
        }
        # None => defer to the convention; a string (including "") is a
        # deliberate per-call override.
        overrides = {k: v for k, v in given.items() if v is not None}
        modes = dict(affix_modes or {})
        if affix_mode:
            # setdefault, not a merge: a per-type entry must still win.
            for _kw, _ck, tk in cls.SUFFIX_BINDINGS:
                modes.setdefault(tk, affix_mode)
        rules = cls.affix_rules(overrides, modes)
        if custom_suffixes:
            for node_type, text in custom_suffixes.items():
                # ``affix_mode`` is documented as placement for EVERY type, so
                # it has to reach a custom one too -- it is seeded from
                # SUFFIX_BINDINGS, which by definition does not list these.
                mode = modes.get(node_type, affix_mode or "auto")
                rules[node_type] = ptk.AffixRule(text or "", mode, node_type)

        # Every affix that may be stripped. ``strip_any_affix`` sorts longest
        # first so '_LSG' wins over '_SG'.
        known = {r.text for r in rules.values() if r.text}
        if strip:
            known.update(ptk.make_iterable(strip))

        objects = cmds.ls(objects, flatten=True, long=True)
        plan = []

        for obj in objects:
            short_name = cls._leaf(obj)
            rule = rules.get(cls.type_key(obj)) or ptk.AffixRule()

            # Strip whichever convention affix the name carries, from either
            # end -- a name written under a previous convention must be
            # corrected, not decorated a second time.
            #
            # Deliberately NOT excluding the affix about to be applied. That
            # exclusion was meant to spare an already-correct name, but
            # apply_affix is idempotent (it strips a pre-existing occurrence
            # first), so it bought nothing -- and it defeated the very flip it
            # sits under: once the convention moves to a prefix, the vocabulary
            # holds "GEO_" and a legacy "body_GEO" matched only that entry, so
            # excluding it left the name unstripped and applied a SECOND affix
            # ("GEO_body_GEO"). Without it: "body_GEO" -> "GEO_body", while an
            # already-correct "GEO_body" still round-trips unchanged.
            base_name = ptk.StrUtils.strip_any_affix(short_name, known)

            # Apply strip_trailing_ints if specified
            if strip_trailing_ints:
                base_name = ptk.format_suffix(
                    base_name,
                    suffix="",
                    strip_trailing_ints=True,
                    strip_trailing_alpha=False,
                )

            if strip_trailing_underscores:
                base_name = re.sub(r"_+$", "", base_name)

            if strip_trailing_padding:
                cleaned = re.sub(r"_+$", "", base_name)
                if cleaned != base_name:
                    # Underscores were at the very end -- also strip now-exposed
                    # trailing digits and any further orphaned underscores.
                    cleaned = re.sub(r"\d+$", "", cleaned)
                    cleaned = re.sub(r"_+$", "", cleaned)
                base_name = cleaned

            # Idempotent: apply() strips a pre-existing copy of this very affix
            # before re-applying, so a correct name is left alone.
            new_name = rule.apply(base_name)

            plan.append((cls._key(obj), short_name, new_name))

        return cls._apply_plan(plan, "Suffix By Type", dry_run)

    @classmethod
    @CoreUtils.undoable
    def append_location_based_suffix(
        cls,
        objects,
        first_obj_as_ref=False,
        alphabetical=False,
        strip_trailing_ints=True,
        strip_defined_suffixes=True,
        valid_suffixes=None,
        reverse=False,
        independent_groups=False,
        dry_run: bool = False,
    ):
        """Rename objects with a suffix defined by its location from origin.

        Parameters:
            objects (str)(int/list): The object(s) to rename.
            first_obj_as_ref (bool): When True, use the first object's bounding box center as reference_point instead of origin.
            alphabetical (str): When True use an alphabetical character as a suffix when there is less than 26 objects else use integers.
            strip_trailing_ints (bool): Strip any trailing integers. ie. 'cube123'
            strip_defined_suffixes (bool): Strip any suffixes found in valid_suffixes kwarg.
            valid_suffixes (list): List of valid suffixes to strip.
            reverse (bool): Reverse the naming order. (Farthest object first)
            independent_groups (bool): When True, objects matching the same base name (after stripping) are grouped and suffixed independently.
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            list[str]: The final names, in distance order (grouped when
                ``independent_groups``).
        """

        objects = cmds.ls(CoreUtils.as_strings(objects), flatten=True)
        if not objects:
            return

        # Determine the reference point
        reference_point = [0, 0, 0]
        if first_obj_as_ref and objects:
            first_obj_bbox = cmds.exactWorldBoundingBox(objects[0])
            reference_point = [
                (first_obj_bbox[i] + first_obj_bbox[i + 3]) / 2 for i in range(3)
            ]

        # Helper to determine if we should strip valid suffixes
        # If independent_groups is True, we generally want to PRESERVE the group suffix (Type)
        # So we force strip_defined_suffixes to False for the base name calculation in that mode.
        strip_suffixes_for_grouping = strip_defined_suffixes
        if independent_groups:
            strip_suffixes_for_grouping = False

        def get_base_name(name):
            # Sort suffixes by length (descending) to match longest first (e.g. match _GRP before _G)
            sorted_suffixes = sorted(valid_suffixes or [], key=len, reverse=True)

            # Loop to handle stacked suffixes (e.g. Name_GRP_01_A)
            # We continue stripping as long as we find something to strip
            while True:
                original_name = name

                # 1. Strip Trailing Integers (e.g. _01, 123)
                if strip_trailing_ints:
                    if name and name[-1].isdigit():
                        # Determine if prefixed with underscore
                        match = re.search(r"(_\d+|\d+)$", name)
                        if match:
                            name = name[: match.start()]

                # 2. Strip Defined Suffixes (e.g. _GRP, _A)
                # Replaces the old "strip_trailing_alpha" logic which only handled single chars
                if strip_suffixes_for_grouping and sorted_suffixes:
                    for suffix in sorted_suffixes:
                        if name.endswith(suffix):
                            name = name[: -len(suffix)]
                            break

                if name == original_name:
                    break

            return name

        newNames = {}
        all_ordered_objs = []

        if independent_groups:
            # Group objects by base name
            groups = {}
            for obj in objects:
                # Use nodeName() to ignore namespace/path for grouping purposes
                # This ensures similar objects in different hierarchies are grouped together
                short_name = obj.split("|")[-1]
                base_name = get_base_name(short_name)

                if base_name not in groups:
                    groups[base_name] = []
                groups[base_name].append(obj)

            for base_name, group_objs in groups.items():
                # Sort group by distance
                ordered_group = XformUtils.order_by_distance(
                    group_objs, reference_point=reference_point, reverse=reverse
                )

                length = len(ordered_group)
                if alphabetical and length <= 26:
                    suffix_list = list(string.ascii_uppercase)[:length]
                else:
                    pad = max(2, len(str(length)))
                    suffix_list = [
                        str(n + 1).zfill(pad) for n in range(length)
                    ]  # 1-based index (01, 02)

                # Determine if checking "Strip Defined Suffixes" should remove the suffix or move it to the end
                # base_name here includes the suffix (e.g. Name_GRP) because strip_suffixes_for_grouping is False
                root_name = base_name
                type_suffix = ""

                # Identify the suffix
                sorted_valid = sorted(valid_suffixes or [], key=len, reverse=True)
                for s in sorted_valid:
                    if base_name.endswith(s):
                        root_name = base_name[: -len(s)]
                        type_suffix = s
                        break

                # If strip_defined_suffixes is True, we discard the suffix (User opted to Strip)
                # If False, we keep it and append it at the end (User opted to Retain/Move)
                if strip_defined_suffixes:
                    type_suffix = ""

                for i, obj in enumerate(ordered_group):
                    # Construct: Root + _Index + Type
                    nm = f"{root_name}_{suffix_list[i]}"
                    if type_suffix:
                        nm = f"{nm}{type_suffix}"

                    newNames[obj] = nm

                all_ordered_objs.extend(ordered_group)
        else:
            ordered_objs = XformUtils.order_by_distance(
                objects, reference_point=reference_point, reverse=reverse
            )

            length = len(ordered_objs)
            if alphabetical and length <= 26:
                suffix_list = list(string.ascii_uppercase)[:length]
            else:
                pad = max(2, len(str(length)))
                suffix_list = [
                    str(n + 1).zfill(pad) for n in range(length)
                ]  # 1-based index

            for n, obj in enumerate(ordered_objs):
                base_name = get_base_name(cls._leaf(obj))
                obj_suffix = suffix_list[n]
                newNames[obj] = base_name + "_" + obj_suffix

            all_ordered_objs = ordered_objs

        # ``order_by_distance`` may return nodes from un-migrated callers;
        # ``cmds`` does not accept those, so str-coerce here.
        plan = [
            (cls._key(str(obj)), cls._leaf(str(obj)), newNames[obj])
            for obj in all_ordered_objs
        ]
        if not dry_run:
            # Park every node that changes on a placeholder first so a target
            # name freed by a later rename in the batch cannot collide into a
            # '1' suffix. Unchanged entries are never renamed back by the plan,
            # so they must keep their name here.
            for key, old, new in plan:
                if new != old:
                    cmds.rename(cls._path(key), "p0000000000")
        return cls._apply_plan(plan, "Suffix By Location", dry_run)

    # ------------------------------------------------------------------
    # Plan execution — shared by every operation
    # ------------------------------------------------------------------

    @staticmethod
    def _leaf(path: str) -> str:
        """The node's leaf name: no DAG path, no namespace."""
        return path.split("|")[-1].split(":")[-1]

    @staticmethod
    def _key(path: str) -> str:
        """A plan key that survives intra-batch path changes (UUID, else the path)."""
        return (cmds.ls(path, uuid=True) or [path])[0]

    @staticmethod
    def _path(key: str) -> str:
        """Resolve a plan key back to the node's current full path."""
        return (cmds.ls(key, long=True) or [key])[0]

    @classmethod
    def _rename_node(cls, key: str, new_name: str) -> str:
        """The :class:`pythontk.RenamePlan` strategy: rename one node, return its new leaf."""
        return cls._leaf(str(cmds.rename(cls._path(key), new_name)))

    @classmethod
    def _node_link(cls, key: str, name: str) -> str:
        """Render a report item as a link that selects the node in the viewport."""
        return cls.log_link(name, "select", node=cls._path(key))

    @classmethod
    def _apply_plan(cls, plan, title: str, dry_run: bool) -> List[str]:
        """Apply ``(key, old_leaf, new_leaf)`` entries and report; returns the resulting node names.

        Live: the node's shortest unique name after the rename (the
        ``cmds.rename`` return, resolved by UUID so it is authoritative even
        after later entries changed the hierarchy). Dry run: the planned leaf.
        Read-only nodes (referenced, locked) are skipped and tallied in one
        line rather than failing one by one.
        """
        read_only = set(cmds.ls(readOnly=True, long=True) or [])
        skipped = {key for key, _old, _new in plan if cls._path(key) in read_only}
        if skipped:
            names = [old for key, old, _new in plan if key in skipped]
            cls.logger.info(
                f"Skipped {len(names)} read-only node(s): {', '.join(names[:10])}"
                f"{', …' if len(names) > 10 else ''}"
            )
        ptk.RenamePlan.apply(
            [e for e in plan if e[0] not in skipped],
            cls._rename_node,
            title=title,
            dry_run=dry_run,
            logger=cls.logger,
            link=cls._node_link,
            unit="object",
        )
        if dry_run:
            return [old if key in skipped else new for key, old, new in plan]
        return [(cmds.ls(key) or [key])[0] for key, _old, _new in plan]


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
