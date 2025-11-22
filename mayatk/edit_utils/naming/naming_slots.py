# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from uitk import Signals

# From this package
from mayatk.edit_utils.naming import Naming


class NamingSlots(Naming, ptk.LoggingMixin):
    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.naming

    @property
    def valid_suffixes(self):
        """Get current valid suffixes from tb003 widget fields."""
        try:
            suffixes = [
                self.ui.tb003.option_box.menu.tb003_txt000.text(),  # Group
                self.ui.tb003.option_box.menu.tb003_txt001.text(),  # Locator
                self.ui.tb003.option_box.menu.tb003_txt002.text(),  # Joint
                self.ui.tb003.option_box.menu.tb003_txt003.text(),  # Mesh
                self.ui.tb003.option_box.menu.tb003_txt004.text(),  # Nurbs Curve
                self.ui.tb003.option_box.menu.tb003_txt005.text(),  # Camera
                self.ui.tb003.option_box.menu.tb003_txt006.text(),  # Light
                self.ui.tb003.option_box.menu.tb003_txt007.text(),  # Display Layer
            ]
            # Filter out empty strings
            return [s for s in suffixes if s]
        except (AttributeError, RuntimeError):
            # Fallback if widgets not initialized or accessed before tb003 exists
            return ["_GRP", "_LOC", "_JNT", "_GEO", "_CRV", "_CAM", "_LGT", "_LYR"]

    def txt000_init(self, widget):
        """ """
        widget.option_box.menu.setTitle("Find")
        # Add clear button to the menu option box
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Case",
            setObjectName="chk000",
            setToolTip="Search case insensitive.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Regular Expression",
            setObjectName="chk001",
            setToolTip="When checked, regular expression syntax is used instead of the default '*' and '|' wildcards.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Locators Only",
            setObjectName="chk007",
            setToolTip="Limit the search to locator objects only.",
        )

    @Signals("returnPressed")
    def txt000(self, widget):
        """Find"""
        # An asterisk denotes startswith*, *endswith, *contains*
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign_case = widget.ui.txt000.option_box.menu.chk000.isChecked()
        locators_only = widget.ui.txt000.option_box.menu.chk007.isChecked()

        text = widget.text()
        if text:
            # First deselect all to avoid issues with the outliner
            pm.select(clear=True)
            # Filter objects based on locators_only option
            if locators_only:
                objects = pm.ls(type="locator")
                # Get the transform nodes for the locator shapes
                objects = [obj.getParent() for obj in objects if obj.getParent()]
            else:
                objects = pm.ls()

            obj_names = [obj.shortName().split("|")[-1] for obj in objects]
            found_names = ptk.find_str(
                text, obj_names, regex=regex, ignore_case=ign_case
            )
            # Map back to original objects
            found_objects = [
                obj for obj, name in zip(objects, obj_names) if name in found_names
            ]
            pm.select(found_objects)

            # Print user-friendly result
            object_type = "locators" if locators_only else "objects"
            if found_objects:
                pm.displayInfo(
                    f"Found and selected {len(found_objects)} {object_type} matching '{text}'"
                )
            else:
                pm.warning(f"No {object_type} found matching '{text}'")

    def txt001_init(self, widget):
        """ """
        widget.option_box.menu.setTitle("Rename")
        # Add clear button to the menu option box
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Retain Suffix",
            setObjectName="chk002",
            setToolTip="Retain the suffix of the selected object(s) if it matches one defined in Suffix By Type.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Find",
            setObjectName="chk008",
            setToolTip="Ignore the find field and rename all matched objects.",
        )

    # The LineEdit text parameter is not emitted on `returnPressed`
    @Signals("returnPressed")
    def txt001(self, widget):
        """Rename"""
        # An asterisk denotes startswith*, *endswith, *contains*
        find = widget.ui.txt000.text()
        to = widget.text()
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign_case = widget.ui.txt000.option_box.menu.chk000.isChecked()
        retain_suffix = widget.ui.txt001.option_box.menu.chk002.isChecked()
        ignore_find = widget.ui.txt001.option_box.menu.chk008.isChecked()

        # Get current valid suffixes from property if retain_suffix is enabled
        valid_suffixes = self.valid_suffixes if retain_suffix else None

        selection = pm.selected() or pm.ls()

        # Count objects before rename
        object_count = len(selection)

        self.rename(
            selection,
            to,
            find if not ignore_find else "",
            regex=regex,
            ignore_case=ign_case,
            retain_suffix=retain_suffix,
            valid_suffixes=valid_suffixes,
        )

        # Print user-friendly result
        filter_info = f" matching '{find}'" if find and not ignore_find else ""
        suffix_info = " (with suffix retention)" if retain_suffix else ""
        pm.displayInfo(
            f"Renamed {object_count} object(s){filter_info} to pattern '{to}'{suffix_info}"
        )

    def tb000_init(self, widget):
        """ """
        widget.option_box.menu.setTitle("Convert Case")
        widget.option_box.menu.add(
            "QComboBox",
            addItems=["capitalize", "upper", "lower", "swapcase", "title"],
            setObjectName="cmb001",
            setToolTip="Set desired python case operator.",
        )

    def tb000(self, widget):
        """Convert Case"""
        case = widget.option_box.menu.cmb001.currentText()

        selection = pm.ls(sl=1)
        objects = selection if selection else pm.ls(objectsOnly=1)
        self.set_case(objects, case)

    def tb001_init(self, widget):
        """ """
        widget.option_box.menu.setTitle("Suffix By Location")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="First Object As Reference",
            setObjectName="chk006",
            setToolTip="Use the first selected object as the reference point, otherwise the scene origin (0,0,0) will be used.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Alphabetical",
            setObjectName="chk005",
            setToolTip="Use an alphabet character as a suffix when there is less than 26 objects, else use integers.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Integers",
            setObjectName="chk002",
            setChecked=True,
            setToolTip="Strip any trailing integers. ie. '123' of 'cube123'",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Alphabetical",
            setObjectName="chk003",
            setChecked=True,
            setToolTip="Strip any trailing uppercase alphabet chars that are prefixed with an underscore.  ie. 'A' of 'cube_A'",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Reverse",
            setObjectName="chk004",
            setToolTip="Reverse the naming order. (Farthest object first)",
        )

    def tb001(self, widget):
        """Suffix By Location"""
        first_obj_as_ref = widget.option_box.menu.chk006.isChecked()
        alphabetical = widget.option_box.menu.chk005.isChecked()
        strip_trailing_ints = widget.option_box.menu.chk002.isChecked()
        strip_trailing_alpha = widget.option_box.menu.chk003.isChecked()
        reverse = widget.option_box.menu.chk004.isChecked()

        selection = pm.ls(sl=True, objectsOnly=True, type="transform")
        self.append_location_based_suffix(
            selection,
            first_obj_as_ref=first_obj_as_ref,
            alphabetical=alphabetical,
            strip_trailing_ints=strip_trailing_ints,
            strip_trailing_alpha=strip_trailing_alpha,
            reverse=reverse,
        )

    def tb002_init(self, widget):
        """ """
        widget.option_box.menu.setTitle("Strip Chars")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Num Chars:",
            setObjectName="s000",
            setValue=1,
            setToolTip="The number of characters to delete.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Trailing",
            setObjectName="chk005",
            setChecked=True,
            setToolTip="Whether to delete characters from the rear of the name.",
        )

    def tb002(self, widget):
        """Strip Chars"""
        sel = pm.selected()
        kwargs = {
            "num_chars": widget.option_box.menu.s000.value(),
            "trailing": widget.option_box.menu.chk005.isChecked(),
        }
        self.strip_chars(sel, **kwargs)

    def tb003_init(self, widget):
        """ """
        widget.option_box.menu.setTitle("Suffix By Type")
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Group Suffix",
            setText="_GRP",
            setObjectName="tb003_txt000",
            setToolTip="Suffix for transform groups.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Locator Suffix",
            setText="_LOC",
            setObjectName="tb003_txt001",
            setToolTip="Suffix for locators.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Joint Suffix",
            setText="_JNT",
            setObjectName="tb003_txt002",
            setToolTip="Suffix for joints.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Mesh Suffix",
            setText="_GEO",
            setObjectName="tb003_txt003",
            setToolTip="Suffix for meshes.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Nurbs Curve Suffix",
            setText="_CRV",
            setObjectName="tb003_txt004",
            setToolTip="Suffix for nurbs curves.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Camera Suffix",
            setText="_CAM",
            setObjectName="tb003_txt005",
            setToolTip="Suffix for cameras.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Light Suffix",
            setText="_LGT",
            setObjectName="tb003_txt006",
            setToolTip="Suffix for lights.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Display Layer Suffix",
            setText="_LYR",
            setObjectName="tb003_txt007",
            setToolTip="Suffix for display layers.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Integers",
            setObjectName="tb003_chk002",
            setChecked=True,
            setToolTip="Strip any trailing integers. ie. '123' of 'cube123'",
        )

    def tb003(self, widget):
        """Suffix By Type"""
        objects = pm.ls(sl=True, objectsOnly=True)

        kwargs = {
            "group_suffix": widget.option_box.menu.tb003_txt000.text(),
            "locator_suffix": widget.option_box.menu.tb003_txt001.text(),
            "joint_suffix": widget.option_box.menu.tb003_txt002.text(),
            "mesh_suffix": widget.option_box.menu.tb003_txt003.text(),
            "nurbs_curve_suffix": widget.option_box.menu.tb003_txt004.text(),
            "camera_suffix": widget.option_box.menu.tb003_txt005.text(),
            "light_suffix": widget.option_box.menu.tb003_txt006.text(),
            "display_layer_suffix": widget.option_box.menu.tb003_txt007.text(),
            "strip_trailing_ints": widget.option_box.menu.tb003_chk002.isChecked(),
        }
        self.suffix_by_type(objects, **kwargs)


# --------------------------------------------------------------------------------------------


# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
