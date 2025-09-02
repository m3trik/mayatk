# !/usr/bin/python
# coding=utf-8
import logging
from typing import Callable, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.display_utils import _display_utils


class Preview:
    def __init__(
        self,
        operation_instance,
        preview_checkbox,
        create_button,
        finalize_func=None,
        message_func=print,
        enable_on_show=False,
        disable_on_hide=True,
    ):
        """Provides an interactive layer for previewing and finalizing operations in a 3D editing environment.

        This class enables real-time previews of operations by linking UI elements to backend functionality.
        It efficiently manages the state and execution of operations, maintaining a clean undo stack and enabling
        rollback of changes during the preview process.

        Parameters:
            operation_instance: The operation instance that contains the preview operation method.
            preview_checkbox: UI element for toggling preview mode.
            create_button: UI element for finalizing the operation.
            finalize_func: Optional function to call when finalizing. Uses operation's finalize if None.
            message_func: Function for displaying messages (default: print).
            enable_on_show: Whether to enable preview when the window is shown.
            disable_on_hide: Whether to disable preview when the window is hidden.
        """
        self.operation_instance = operation_instance
        self.preview_checkbox = preview_checkbox
        self.create_button = create_button
        self.finalize_func = finalize_func
        self.message_func = message_func
        self.logger = logging.getLogger(__name__)

        # Preview state tracking
        self.is_enabled = False
        self.preview_active = False
        self.undo_chunk_id = None

        # Window behavior settings
        self.enable_on_show = enable_on_show
        self.disable_on_hide = disable_on_hide
        self.window = None

        # Connect UI elements
        self.setup_ui_connections()

    def setup_ui_connections(self):
        """Connect UI elements to preview functionality."""
        if self.preview_checkbox:
            self.preview_checkbox.toggled.connect(self.toggle_preview)

        if self.create_button:
            self.create_button.clicked.connect(self.finalize)

    def enable(self):
        """Enable preview mode."""
        if self.is_enabled:
            return

        self.is_enabled = True
        self.logger.info("Preview mode enabled")

    def disable(self):
        """Disable preview mode and clean up any active preview."""
        if not self.is_enabled:
            return

        # Clean up any active preview
        if self.preview_active:
            self._cleanup_preview()

        self.is_enabled = False
        self.logger.info("Preview mode disabled")

    def toggle_preview(self, state):
        """Toggle preview state based on checkbox."""
        if not self.is_enabled:
            return

        if state and not self.preview_active:
            self._start_preview()
        elif not state and self.preview_active:
            self._cleanup_preview()

    def refresh(self):
        """Refresh the current preview."""
        if self.preview_active:
            # Simply restart the preview - undo will handle the cleanup
            self._cleanup_preview()
            self._start_preview()

    def _start_preview(self):
        """Start a new preview operation."""
        try:
            # Mark the starting point for undo
            pm.undoInfo(openChunk=True)
            self.undo_chunk_id = pm.undoInfo(query=True, chunkName=True)

            # Execute the preview operation
            if hasattr(self.operation_instance, "preview_operation"):
                self.operation_instance.preview_operation()
            elif hasattr(self.operation_instance, "__call__"):
                self.operation_instance()
            else:
                self.logger.warning("No preview operation method found")
                return

            self.preview_active = True
            self.logger.info("Preview started")

        except Exception as e:
            # If preview fails, close the undo chunk
            if pm.undoInfo(query=True, state=True):
                pm.undoInfo(closeChunk=True)
            self.logger.error(f"Preview failed: {e}")
            raise

    def _cleanup_preview(self):
        """Clean up the current preview by undoing to the marked point."""
        if not self.preview_active:
            return

        try:
            # Close the current undo chunk and undo it
            if pm.undoInfo(query=True, state=True):
                pm.undoInfo(closeChunk=True)

            # Undo the preview operations
            pm.undo()

            self.preview_active = False
            self.undo_chunk_id = None
            self.logger.info("Preview cleaned up")

        except Exception as e:
            self.logger.error(f"Failed to cleanup preview: {e}")
            # Force cleanup
            self.preview_active = False
            self.undo_chunk_id = None

    def finalize(self):
        """Finalize the current preview operation."""
        if not self.preview_active:
            # No active preview, just run the operation normally
            try:
                if self.finalize_func:
                    self.finalize_func()
                elif hasattr(self.operation_instance, "finalize"):
                    self.operation_instance.finalize()
                elif hasattr(self.operation_instance, "__call__"):
                    self.operation_instance()
                else:
                    self.logger.warning("No finalize method found")

                self.logger.info("Operation finalized (no preview)")
            except Exception as e:
                self.logger.error(f"Finalization failed: {e}")
                raise
        else:
            # Preview is active, just close the undo chunk to keep the changes
            try:
                if pm.undoInfo(query=True, state=True):
                    pm.undoInfo(closeChunk=True)

                self.preview_active = False
                self.undo_chunk_id = None

                # Disable preview checkbox
                if self.preview_checkbox:
                    self.preview_checkbox.setChecked(False)

                self.logger.info("Preview finalized")

            except Exception as e:
                self.logger.error(f"Failed to finalize preview: {e}")
                raise

    def conditionally_enable(self):
        """Enable preview if enable_on_show is True."""
        if self.enable_on_show:
            self.enable()

    def conditionally_disable(self):
        """Disable preview if disable_on_hide is True."""
        if self.disable_on_hide:
            self.disable()

    def init_show_hide_behavior(self, enable_on_show, disable_on_hide):
        """Initialize window show/hide behavior."""
        self.enable_on_show = enable_on_show
        self.disable_on_hide = disable_on_hide

        if self.window and hasattr(self.window, "on_show"):
            try:
                self.window.on_show.connect(self.conditionally_enable)
            except Exception as e:
                self.logger.warning(f"Could not connect to on_show signal: {e}")

        if self.window and hasattr(self.window, "on_hide"):
            try:
                self.window.on_hide.connect(self.conditionally_disable)
            except Exception as e:
                self.logger.warning(f"Could not connect to on_hide signal: {e}")

    def get_current_state(self):
        """Get the current state of the preview system."""
        return {
            "is_enabled": self.is_enabled,
            "preview_active": self.preview_active,
            "undo_chunk_id": self.undo_chunk_id,
        }
