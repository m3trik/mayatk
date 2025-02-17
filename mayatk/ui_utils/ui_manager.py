# !/usr/bin/python
# coding=utf-8
from typing import Optional
import pythontk as ptk
from uitk import Switchboard
from mayatk import UiUtils


class UiManager:
    """Manages a persistent instance of the Switchboard UI."""

    _instance: Optional[Switchboard] = None  # Stores the single instance

    @ptk.ClassProperty
    def instance(cls) -> Switchboard:
        """Singleton instance of Switchboard, created on first access.

        Returns:
            (Switchboard): The singleton instance.
        """
        if cls._instance is None:
            cls._instance = Switchboard(parent=UiUtils.get_main_window())
        return cls._instance

    @classmethod
    def resolve_ui_name(cls, ui_source: str) -> str:
        """Resolve the UI name from the given source path or identifier.

        Parameters:
            ui_source (str): Path to the UI file or identifier.

        Returns:
            (str): The resolved UI name.
        """
        return ptk.format_path(ui_source, "name")

    @classmethod
    def get_ui(cls, ui_source, slot_source=None) -> object:
        """Get a UI instance from the Switchboard, or register a new one.

        Parameters:
            ui_source (str): Path to the UI file or identifier.
            slot_source (obj): Slot source for the UI.

        Returns:
            (obj): The UI instance.
        """
        sb = cls.instance  # Access the singleton instance via property
        name = cls.resolve_ui_name(ui_source)

        try:
            return sb.get_ui(name)
        except AttributeError:
            if slot_source is None:
                raise ValueError("Slot source is required for UI initialization.")

            sb.register(ui_source, slot_source, base_dir=slot_source)
            ui = sb.get_ui(name)

            ui.set_attributes(WA_TranslucentBackground=True)
            ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
            ui.set_style(theme="dark", style_class="translucentBgWithBorder")
            ui.header.configure_buttons(
                menu_button=True, minimize_button=True, hide_button=True
            )
            return ui


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import example

    ui_name = "example"
    ui_file = os.path.join(os.path.dirname(__file__), f"{ui_name}.ui")
    ui = UiManager.get_ui(ui_file, example.ExampleSlots)
    ui.show(pos="screen", app_exec=True)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
