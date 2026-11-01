# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from typing import List, Union, Optional, Dict, Tuple
import re

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils


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

    #: The convention entries this host has no type for, ``{convention key:
    #: why}``: :meth:`suffix_by_type` never applies them, and the Naming panel
    #: greys their rows. Every entry names a Maya node type, so none here
    #: (blendertk's lists its gaps).
    UNSUPPORTED_TYPES: Dict[str, str] = {}

    #: The DG node families of :attr:`SUFFIX_BINDINGS`, ``{type key: cmds.ls
    #: filter}``: with the DAG transforms, what the Scene scope holds (a type
    #: key with no row here names a transform, a joint or a constraint). Never
    #: a script node, a renderer singleton or construction history.
    _DG_FAMILIES: Dict[str, Dict[str, object]] = {
        "displayLayer": {"type": "displayLayer"},
        "cluster": {"type": "cluster"},
        "lattice": {"type": "ffd"},
        "skinCluster": {"type": "skinCluster"},
        "blendShape": {"type": "blendShape"},
        "material": {"materials": True},
        "shadingEngine": {"type": "shadingEngine"},
        "texture": {"textures": True},
        "objectSet": {"type": "objectSet"},
    }

    #: What a rename pattern holding ``#`` is told: Maya's own ``rename``
    #: counts with a trailing ``#``, this tool does not (a taken name is
    #: numbered as Maya numbers it).
    HASH_NOTE = (
        "'#' is not a counter here: it is replaced by '_'. A name already taken "
        "is numbered the way Maya numbers it (box, box1, box2)."
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
        if "#" in to:
            cls.logger.warning(cls.HASH_NOTE)

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
    def _user_nodes(cls) -> List[str]:
        """The user's own nodes, referenced ones included: every DAG transform
        and the DG families :attr:`SUFFIX_BINDINGS` names (:attr:`_DG_FAMILIES`),
        less Maya's default and undeletable nodes (the startup cameras, the
        managers). What Find searches."""
        found = list(cmds.ls(type="transform", long=True) or [])
        keys = {tk for _kw, _ck, tk in cls.SUFFIX_BINDINGS}
        for key, family in cls._DG_FAMILIES.items():
            if key in keys:
                found += cmds.ls(long=True, **family) or []
        skip = set(cmds.ls(defaultNodes=True, long=True) or [])
        skip.update(cmds.ls(undeletable=True, long=True) or [])
        return [n for n in dict.fromkeys(found) if n not in skip]

    @classmethod
    def scene_objects(cls) -> List[str]:
        """Every renameable node in the scene — the naming tools' "Scene" scope.

        An allowlist of the user's objects: the DAG transforms (groups,
        meshes, curves, cameras, lights, joints, constraints, ...) and the DG
        node families the Suffix By Type bindings name (materials, shading
        groups, textures, sets, display layers, deformers). Never a script
        node, a renderer's singleton (renaming ``defaultArnoldRenderOptions``
        made MtoA create a fresh one: the render settings were lost) or
        construction history. Maya's default, undeletable and read-only nodes
        are left out, and shapes (a shape follows its transform's name;
        renaming both would double-suffix).

        Returns:
            list[str]: Long names.
        """
        nodes = cls._user_nodes()
        read_only = set(cmds.ls(nodes, readOnly=True, long=True) or []) if nodes else set()
        return [n for n in nodes if n not in read_only]

    @classmethod
    def scope_objects(cls, scene: bool = False) -> List[str]:
        """What a naming operation acts on in a scene scope: every renameable
        node (*scene*, :meth:`scene_objects`), else the current selection.

        Returns:
            list[str]: Node UUIDs, which every operation takes as it takes a
            name: a plan frozen now (a dry run's Apply) still finds each node
            after a rename moved its path -- a parent's included.
        """
        nodes = cls.scene_objects() if scene else cmds.ls(selection=True, long=True)
        # ls([]) lists the whole scene: never hand it nothing.
        return list(dict.fromkeys(cmds.ls(nodes, uuid=True) or [])) if nodes else []

    @classmethod
    def select_by_name(
        cls,
        pattern: str,
        regex: bool = False,
        ignore_case: bool = False,
        locators_only: bool = False,
    ) -> Tuple[List[str], int]:
        """Select the scene nodes whose name matches *pattern* (Find).

        Searches the user's own nodes -- what the Scene scope holds
        (:meth:`scene_objects`), referenced nodes included -- never Maya's
        defaults, history or system nodes. Matches each node's leaf name (no
        path) through :func:`pythontk.find_str`, without its namespace unless
        *pattern* names one (``ns:*``); the matches replace the selection, and
        none found clears it.

        Parameters:
            pattern: ``*`` / ``|`` wildcards (``startswith*``, ``*contains*``,
                ``a|b``), or a regular expression with *regex*.
            regex: Read *pattern* as a regular expression.
            ignore_case: Match case-insensitively.
            locators_only: Search locator transforms only.

        Returns:
            tuple: ``(matches, searched)`` -- the matched nodes (long names),
            now the selection, and how many nodes were searched.
        """
        cmds.select(clear=True)  # first, so the outliner never shows a stale mix
        if locators_only:
            shapes = cmds.ls(type="locator", long=True) or []
            # listRelatives([]) falls back to the selection -- never pass it
            # nothing. allParents: an instanced locator has a parent per instance.
            objects = list(
                dict.fromkeys(
                    cmds.listRelatives(shapes, allParents=True, fullPath=True) or []
                    if shapes
                    else []
                )
            )
        else:
            objects = cls._user_nodes()
        # A namespace named: match it too ('ns:*'). A regex group's '(?:' or
        # '(?i:' names none.
        if ":" in (re.sub(r"\(\?[A-Za-z-]*:", "(", pattern) if regex else pattern):
            names = [o.split("|")[-1] for o in objects]
        else:
            names = [cls._leaf(o) for o in objects]
        hits = set(ptk.find_str(pattern, names, regex=regex, ignore_case=ignore_case))
        found = [o for o, name in zip(objects, names) if name in hits]
        if found:
            cmds.select(found)
        return found, len(objects)

    @classmethod
    def select_link(cls, node: str) -> str:
        """A report link labeled with *node*'s leaf name that selects it."""
        return cls.log_link(cls._leaf(node), "select", node=node)

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
        (Maya's own spelling: ``prop1`` → ``propShape1``), with ``Orig``
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

            # A digit left in front is kept: the plan step legalizes it
            # ('a1box' minus 'a' -> '_1box'), and skips a name left empty.
            new_name = s[:-num_chars] if trailing else s[num_chars:]
            plan.append((cls._key(obj), s, new_name))

        return cls._apply_plan(plan, "Strip Chars", dry_run)

    @classmethod
    @CoreUtils.undoable
    def set_case(cls, objects=None, case="capitalize", dry_run: bool = False):
        """Rename objects following the given case.

        Parameters:
            objects (str/list): The objects to rename. default: the Scene
                scope (:meth:`scene_objects`).
            case (str): Desired case, a :meth:`pythontk.StrUtils.set_case` case:
                    'upper', 'lower', 'capitalize', 'swapcase', 'title' (Python's,
                    which lower every other letter), or 'upper_first' /
                    'upper_initials' (first letters raised, the rest kept).
                    default:'capitalize'
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            List[str]: The names after the operation, parallel to the objects.

        Example:
            set_case(cmds.ls(sl=1), 'upper')
        """
        objects = cmds.ls(objects, long=True) if objects else cls.scene_objects()
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
    def affix_for(
        cls,
        obj: str,
        overrides: Optional[Dict[str, str]] = None,
        modes: Optional[Dict[str, str]] = None,
    ) -> "ptk.AffixRule":
        """The convention's affix rule for *obj*'s OWN type.

        The single-node form of the join :meth:`suffix_by_type` runs in bulk:
        :meth:`type_key` says which type this host considers the node, and
        :meth:`affix_rules` says what the shared convention calls that type.

        Any tool that names a node the USER handed it -- rather than one it
        created and therefore already knows the type of -- should ask this
        instead of hard-coding a spelling. A camera then comes out ``_CAM``
        where a mesh comes out ``_GEO``, and a studio that renames either moves
        both without the tool being touched.

        Parameters:
            obj: The node to classify.
            overrides: Per-call affix overrides -- see :meth:`affix_rules`.
            modes: Per-call placement overrides -- see :meth:`affix_rules`.

        Returns:
            ptk.AffixRule: The rule for the node's type; ``.text`` is the
            spelling and ``.apply(name)`` places it on the correct side. An
            empty (no-op) rule when the type has no convention entry.
        """
        rules = cls.affix_rules(overrides, modes)
        return rules.get(cls.type_key(obj)) or ptk.AffixRule()

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
            #
            # numbered: a duplicate's '1' (Ctrl+D of crate_GEO is crate_GEO1)
            # or an index after the affix (wall_GEO_01) is peeled first and
            # kept on the base, so the result is crate1_GEO / wall_01_GEO
            # rather than a second affix.
            base_name = ptk.StrUtils.strip_any_affix(short_name, known, numbered=True)

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
        order: str = "distance",
        dry_run: bool = False,
    ):
        """Rename objects with a suffix numbering them by location.

        Each object's location is its pivot (the world rotate pivot), as
        blendertk reads ``matrix_world.translation``, so both hosts number one
        layout the same way -- and an empty group counts where it stands, not
        at its empty bounding box's origin. The order is
        :meth:`pythontk.PointCloud.order_points`'s: deterministic, float noise
        quantized away, a tie never left to the selection order.

        Parameters:
            objects (str)(int/list): The object(s) to rename. Only transforms
                have a location to order by; anything else is skipped.
            first_obj_as_ref (bool): With ``order="distance"``, measure from the
                first object's pivot instead of the world origin.
            alphabetical (str): When True use an alphabetical character as a suffix when there is less than 26 objects else use integers.
                A letter this tool wrote (the batch's letters run A, B, C, ...)
                is stripped first, so a re-run is stable; any other trailing
                letter ('_L', '_R') is name content.
            strip_trailing_ints (bool): Strip any trailing integers. ie. 'cube123'
            strip_defined_suffixes (bool): Strip any suffixes found in valid_suffixes kwarg.
            valid_suffixes (list): List of valid suffixes to strip.
            reverse (bool): Reverse the naming order (the farthest, or the far
                end of the axis, first).
            independent_groups (bool): When True, objects matching the same base name (after stripping) are grouped and suffixed independently.
            order (str): What the numbers follow, one of
                :attr:`pythontk.PointCloud.ORDERS`: ``"distance"`` (the default:
                from the origin, or the first object), ``"x"`` / ``"y"`` /
                ``"z"`` (signed position along that world axis; ties go to the
                other axes, then the name), or ``"auto"`` (the axis the objects
                are most spread along, so a row is numbered along the row).
            dry_run (bool): Plan and report the renames without changing the scene.

        Returns:
            list[str]: The final names, in order (grouped when
                ``independent_groups``); ``[]`` with no transform to rename.
        """
        # A scene scope also holds shapes, shaders and sets: none of them has a
        # location, so only the transforms are numbered.
        objects = (
            cmds.ls(
                CoreUtils.as_strings(objects), type="transform", flatten=True, long=True
            )
            or []
        )
        if not objects:
            cls.logger.warning("No transforms to suffix by location.")
            return []

        positions = {
            obj: tuple(cmds.xform(obj, q=True, worldSpace=True, rotatePivot=True))
            for obj in objects
        }
        order = (order or "distance").lower()
        if order == "auto":  # one axis for the whole batch, groups included
            order = ptk.PointCloud.spread_axis(list(positions.values()))
        reference = positions[objects[0]] if first_obj_as_ref else (0.0, 0.0, 0.0)

        def ordered(objs):
            return ptk.PointCloud.order_points(
                [(o, positions[o]) for o in objs],
                order_by=order,
                reference=reference,
                reverse=reverse,
            )

        # The host-free plan (strip what a previous run left, group, number)
        # is pythontk's, written once for both hosts.
        new_names = ptk.StrUtils.sequence_names(
            {obj: cls._leaf(obj) for obj in objects},
            order=ordered,
            alphabetical=alphabetical,
            strip_trailing_ints=strip_trailing_ints,
            strip_defined_suffixes=strip_defined_suffixes,
            valid_suffixes=valid_suffixes,
            independent_groups=independent_groups,
        )

        plan = [
            (cls._key(obj), cls._leaf(obj), name) for obj, name in new_names.items()
        ]
        title = "Suffix By Location"
        if order in ("x", "y", "z"):
            title += f" (along {order.upper()})"
        # Two phases (no target collides with a name the batch still holds)
        # are the shared plan step's, after its skip filter.
        return cls._apply_plan(plan, title, dry_run)

    # ------------------------------------------------------------------
    # Plan execution — shared by every operation
    # ------------------------------------------------------------------

    @staticmethod
    def _leaf(path: str) -> str:
        """The node's leaf name: no DAG path, no namespace."""
        return path.split("|")[-1].split(":")[-1]

    @staticmethod
    def _namespace(path: str) -> str:
        """The node's own namespace: ``a:b`` of ``|grp|a:b:box``, ``""`` at the root."""
        return path.split("|")[-1].rpartition(":")[0]

    @staticmethod
    def _in_namespace(namespace: str, name: str) -> str:
        """*name* spelled absolute in *namespace* -- what ``cmds.rename`` needs
        to keep a node where it is: a bare name lands in the CURRENT namespace
        (``ns:prop1`` renamed ``PROP1`` became ``|PROP1``)."""
        return f":{namespace}:{name}" if namespace else f":{name}"

    @staticmethod
    def _key(path: str) -> str:
        """A plan key that survives intra-batch path changes (UUID, else the path)."""
        return (cmds.ls(path, uuid=True) or [path])[0]

    @staticmethod
    def _path(key: str) -> str:
        """Resolve a plan key back to the node's current full path."""
        return (cmds.ls(key, long=True) or [key])[0]

    @staticmethod
    def legalize(name: str) -> str:
        """*name* as a Maya node name, or ``""`` when it holds no name at all.

        Every operation's planned names pass through here (the shared plan
        step): each character outside letters, digits and ``_`` becomes ``_``,
        and a leading digit gets a ``_`` in front -- Maya's own rename drops
        it (``1box`` -> ``box``). A name of underscores alone (``---``) or
        nothing is no name: the plan step skips it, with a warning. ``#`` is
        replaced like any other character (see :attr:`HASH_NOTE`).

        Example:
            legalize("1box") --> '_1box'
            legalize("---") --> ''
        """
        legal = ptk.StrUtils.to_identifier(name)
        return legal if legal.strip("_") else ""

    @classmethod
    def _rename_node(cls, key: str, new_name: str) -> str:
        """The :class:`pythontk.RenamePlan` strategy: rename one node, in its
        own namespace, and return its new leaf."""
        path = cls._path(key)
        target = cls._in_namespace(cls._namespace(path), new_name)
        return cls._leaf(str(cmds.rename(path, target)))

    @classmethod
    def _park(cls, key: str, index: int) -> None:
        """The two-phase run's placeholder (:meth:`pythontk.RenamePlan.apply`'s ``park``)."""
        cls._rename_node(key, f"naming_park_{index}")

    @classmethod
    def _node_link(cls, key: str, name: str) -> str:
        """Render a report item as a link that selects the node in the viewport."""
        return cls.log_link(name, "select", node=cls._path(key))

    @staticmethod
    def _uniquify(name: str, taken) -> str:
        """Maya's rule for a taken *name*, measured in Maya 2025: one more than
        the highest number any ``<base><N>`` in the pool carries (the name's
        own counts; none is 0), padded as the name was -- so past ``box`` and
        ``box2`` comes ``box3`` (never the free ``box1``), and ``box_01`` gives
        ``box_02``."""
        base, digits = re.match(r"^(.*?)(\d*)$", name).groups()
        pattern = re.compile(re.escape(base) + r"(\d+)$")
        top = int(digits or 0)
        for other in taken:
            m = pattern.match(other)
            if m:
                top = max(top, int(m.group(1)))
        return f"{base}{str(top + 1).zfill(len(digits))}"

    @classmethod
    def _predict(cls, plan) -> List[str]:
        """The names Maya gives *plan*'s entries on collision, in order
        (:meth:`pythontk.RenamePlan.predict`), so a dry run previews the live
        run. Maya's pools, measured: a DAG node's name must be free among its
        siblings and among every DG node's; a DG node's among every node's
        (DAG nodes included, at any depth); each namespace is its own."""
        if not plan:
            return []
        paths = {key: cls._path(key) for key, _old, _new in plan}
        dag = set(cmds.ls(list(paths.values()), dag=True, long=True) or [])
        scene = {}

        def every():
            if not scene:
                scene["all"] = cmds.ls(long=True) or []
                scene["dag"] = set(cmds.ls(dag=True, long=True) or [])
            return scene["all"], scene["dag"]

        def scopes(key):
            path = paths[key]
            ns = cls._namespace(path)
            if path in dag:
                siblings = ("siblings", path.rpartition("|")[0], ns)
                return [siblings, ("dg", ns)], [siblings, ("all", ns)]
            return [("all", ns)], [("dg", ns), ("all", ns)]

        def names(pool):
            kind, ns = pool[0], pool[-1]
            if kind == "siblings":
                parent = pool[1]
                members = (
                    cmds.listRelatives(parent, children=True, fullPath=True)
                    if parent
                    else cmds.ls(assemblies=True, long=True)
                ) or []
            else:
                nodes, dag_nodes = every()
                members = nodes if kind == "all" else [n for n in nodes if n not in dag_nodes]
            return [cls._leaf(m) for m in members if cls._namespace(m) == ns]

        return ptk.RenamePlan.predict(plan, scopes, names, cls._uniquify)

    @classmethod
    def _followers(cls, plan) -> set:
        """The keys of the shapes in *plan* whose parent transform is in it too.

        Maya renames a shape along with its transform (``seat`` ->
        ``seat_GEO`` carries ``seatShape`` to ``seat_GEOShape``), so renaming
        the shape as well only decorated it (``seatShape_GEO``, from a
        ``select -hi`` or a Find of ``*seat*``). A shape not named after its
        transform keeps its name, as in Maya (:meth:`conform_shape_names`
        repairs those).
        """
        keys = {key for key, _old, _new in plan}
        paths = [cls._path(key) for key in keys]
        shapes = (cmds.ls(paths, shapes=True, long=True) or []) if paths else []
        followers = set()
        for shape in shapes:
            parents = cmds.listRelatives(shape, allParents=True, fullPath=True) or []
            if any(cls._key(parent) in keys for parent in parents):
                followers.add(cls._key(shape))
        return followers

    @classmethod
    def _locked(cls, plan) -> set:
        """The keys of *plan*'s nodes Maya will not rename: read-only
        (referenced) and lockNode-locked nodes, and Maya's default and
        undeletable ones (the startup cameras, ``lambert1``, ``time1``, the
        managers)."""
        by_path = {cls._path(key): key for key, _old, _new in plan}
        nodes = [n for n in by_path if cmds.objExists(n)]
        if not nodes:
            return set()
        blocked = set(cmds.ls(nodes, readOnly=True, long=True) or [])
        blocked.update(cmds.ls(defaultNodes=True, long=True) or [])
        blocked.update(cmds.ls(undeletable=True, long=True) or [])
        locks = cmds.lockNode(nodes, query=True, lock=True) or []
        blocked.update(n for n, locked in zip(nodes, locks) if locked)
        return {key for path, key in by_path.items() if path in blocked}

    @staticmethod
    def _listed(items, limit: int = 10) -> str:
        """``a, b, …`` -- a tally line's names, the first *limit* of them."""
        shown = ", ".join(items[:limit])
        return f"{shown}, …" if len(items) > limit else shown

    @classmethod
    def _apply_plan(cls, plan, title: str, dry_run: bool) -> List[str]:
        """Apply ``(key, old_leaf, new_leaf)`` entries and report; returns the resulting node names.

        The plan step every operation shares, so each behaves the same:

        1. A shape whose transform is in the batch follows it and is dropped
           (:meth:`_followers`).
        2. Each new name is legalized (:meth:`legalize`); one with no name
           left is skipped, all of them in one warning.
        3. What Maya will not rename is skipped, in one line (:meth:`_locked`).
        4. The names Maya will number on a collision are predicted
           (:meth:`_predict`) and planned as such, so a dry run shows what the
           live run does; they are reported in one line.
        5. The batch runs in two phases (:meth:`pythontk.RenamePlan.apply`
           with ``park``), so no rename meets a name the batch still holds and
           nothing is ever left on a placeholder.

        Returns, parallel to *plan*: live, each node's shortest unique name
        after the rename (resolved by UUID, so it is authoritative after later
        entries changed the hierarchy); on a dry run, the predicted leaf (the
        old leaf for a node skipped or following its transform).
        """
        plan = list(plan)
        followers = cls._followers(plan)
        staged, nameless = [], []
        for key, old, new in plan:
            if key in followers:
                continue
            if new != old:
                legal = cls.legalize(new)
                if not legal:
                    nameless.append(f"{old} → '{new}'")
                    continue
                new = legal
            staged.append((key, old, new))
        if nameless:
            cls.logger.warning(
                f"Skipped {len(nameless)} name(s) with no letters or digits left: "
                f"{cls._listed(nameless)}"
            )
        locked = cls._locked(staged)
        if locked:
            names = [old for key, old, _new in staged if key in locked]
            cls.logger.info(
                f"Skipped {len(names)} node(s) Maya will not rename (default, "
                f"locked or read-only): {cls._listed(names)}"
            )
        live = [entry for entry in staged if entry[0] not in locked]
        predicted = cls._predict(live)
        numbered = [
            f"{old} → {got}"
            for (_key, old, new), got in zip(live, predicted)
            if got != new
        ]
        if numbered:
            cls.logger.warning(
                f"{len(numbered)} name(s) already taken, numbered as Maya numbers "
                f"them: {cls._listed(numbered)}"
            )
        live = [(key, old, got) for (key, old, _new), got in zip(live, predicted)]
        ptk.RenamePlan.apply(
            live,
            cls._rename_node,
            title=title,
            dry_run=dry_run,
            logger=cls.logger,
            link=cls._node_link,
            unit="object",
            park=cls._park,
        )
        if dry_run:
            planned = {key: new for key, _old, new in live}
            return [planned.get(key, old) for key, old, _new in plan]
        return [(cmds.ls(key) or [key])[0] for key, _old, _new in plan]


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
