# !/usr/bin/python
# coding=utf-8
import contextlib
from inspect import signature
from typing import Dict, Any


class TaskFactory:
    """A factory class for managing and executing tasks in a scene export pipeline."""

    def __init__(self, logger):
        self.logger = logger
        self._method_cache = {}

    def _get_cached_method(self, method_name: str):
        """Get method with caching to avoid repeated getattr calls."""
        if method_name not in self._method_cache:
            self._method_cache[method_name] = getattr(self, method_name, None)
        return self._method_cache[method_name]

    @contextlib.contextmanager
    def _manage_context(self, tasks: Dict[str, Any]) -> Dict[str, Any]:
        """Manage task states by setting them once and reverting after, returning task results."""
        original_states = {}
        task_results = {}

        # Pre-validate and cache all methods
        valid_tasks = {}
        for name, value in tasks.items():
            method = self._get_cached_method(name)
            if method:
                valid_tasks[name] = value
            else:
                self.logger.warning(f"Missing method: {name}. Skipping.")

        if not valid_tasks:
            yield {}
            return

        self.logger.info(f"Running {len(valid_tasks)} tasks")

        for index, (task_name, value) in enumerate(valid_tasks.items(), start=1):
            method = self._method_cache[task_name]  # Already cached
            self.logger.debug(
                f"Executing Task #{index}/{len(valid_tasks)}: {task_name}"
            )

            # Get revert method BEFORE executing the task
            revert_method = self._get_revert_method(task_name)

            try:
                result = self._execute_task_method(method, value)
                task_results[task_name] = result

                # Store original state for reversion if this is a "set_" task
                if revert_method and result is not None:
                    original_states[task_name] = {
                        "revert_method": revert_method,
                        "original_value": result,
                    }
                    self.logger.debug(
                        f"Stored original state for {task_name}: {result}"
                    )

                # Handle check failures efficiently
                if task_name.startswith("check_") and not self._is_success(result):
                    self._log_check_failed(task_name, self._get_log_messages(result))

            except Exception as e:
                self.logger.error(f"Error during task {task_name}: {e}")
                raise

        yield task_results
        self._revert_states(original_states)

    def _execute_task_method(self, method, value: Any):
        """Execute a task method with proper parameter handling."""
        try:
            sig = signature(method)
            param_count = len(
                [
                    p
                    for p in sig.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                ]
            )

            if param_count == 0:
                return method()
            elif param_count == 1:
                return method(value)
            else:
                # Handle methods that accept multiple parameters
                if isinstance(value, (list, tuple)):
                    return method(*value)
                elif isinstance(value, dict):
                    return method(**value)
                else:
                    return method(value)

        except TypeError as e:
            self.logger.error(f"Parameter mismatch for {method.__name__}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Error executing task method {method.__name__}: {e}")
            raise

    def run_tasks(self, tasks: Dict[str, Any]) -> bool:
        """Run tasks and checks, returning True if all checks pass, False if any fail."""
        if not tasks:
            self.logger.notice("No tasks provided to run.")
            return True

        # Split tasks and checks
        tasks_only = {k: v for k, v in tasks.items() if not k.startswith("check_")}
        checks_only = {k: v for k, v in tasks.items() if k.startswith("check_")}

        return self._execute_tasks_and_checks(tasks_only, checks_only)

    def run_tasks_by_category(
        self, task_definitions: Dict[str, Any], check_definitions: Dict[str, Any]
    ) -> bool:
        """Alternative method to run tasks and checks separately with better organization."""
        return self._execute_tasks_and_checks(task_definitions, check_definitions)

    def _execute_tasks_and_checks(
        self,
        tasks_only: Dict[str, Any],
        checks_only: Dict[str, Any],
    ) -> bool:
        """Execute tasks and checks with unified logic."""
        failed_checks = []
        all_checks_passed = True

        # Run tasks first
        if tasks_only:
            sorted_tasks = (
                tasks_only
                if self._is_sorted(tasks_only)
                else dict(sorted(tasks_only.items()))
            )
            self.logger.info(f"Running {len(sorted_tasks)} export tasks...")
            with self._manage_context(sorted_tasks):
                pass

        # Run checks second
        if checks_only:
            sorted_checks = (
                checks_only
                if self._is_sorted(checks_only)
                else dict(sorted(checks_only.items()))
            )
            self.logger.info(f"Running {len(sorted_checks)} validation checks...")

            with self._manage_context(sorted_checks) as check_results:
                all_checks_passed = self._process_check_results(
                    check_results, checks_only, failed_checks
                )

        self._log_execution_summary(
            failed_checks, all_checks_passed, len(tasks_only), len(checks_only)
        )
        return all_checks_passed

    def _is_sorted(self, d: Dict[str, Any]) -> bool:
        """Check if dictionary keys are already sorted."""
        keys = list(d.keys())
        return keys == sorted(keys)

    def _process_check_results(
        self,
        check_results: Dict[str, Any],
        checks_only: Dict[str, Any],
        failed_checks: list,
    ) -> bool:
        """Process check results and return overall success status."""
        all_checks_passed = True

        for index, (check_name, result) in enumerate(check_results.items(), start=1):
            progress = f"[{index}/{len(checks_only)}]"

            success = result[0] if isinstance(result, tuple) else result

            if not success:
                failed_checks.append(check_name)
                all_checks_passed = False
            else:
                self.logger.success(f"{progress} Check passed: {check_name}")

        return all_checks_passed

    def _log_execution_summary(
        self,
        failed_checks: list,
        all_checks_passed: bool,
        tasks_count: int,
        checks_count: int,
    ) -> None:
        """Log the execution summary."""
        if not all_checks_passed:
            self.logger.log_box(
                "SUMMARY OF FAILED CHECKS",
                [f"- {check}" for check in failed_checks],
            )
            self.logger.error("Export aborted due to failed checks.")
        else:
            self._log_success_summary(tasks_count, checks_count, len(failed_checks))

    def _log_success_summary(
        self, tasks_count: int, checks_count: int, failed_checks_count: int
    ) -> None:
        """Log a simple success summary box."""
        summary_lines = []

        # Export status
        if checks_count > 0:
            summary_lines.append("✓ All tasks and checks completed successfully")
        else:
            summary_lines.append("✓ All tasks completed successfully")

        summary_lines.append("")  # Empty line for spacing

        # Simple counts in x/y format
        summary_lines.append(f"Tasks Executed: {tasks_count}/{tasks_count}")
        if checks_count > 0:
            checks_passed = checks_count - failed_checks_count
            summary_lines.append(f"Checks Passed: {checks_passed}/{checks_count}")

        self.logger.log_box("EXPORT SUCCESSFUL", summary_lines)

    def _log_check_failed(self, task_name: str, log_messages: list):
        """Log the 'CHECK FAILED' box after task fails."""
        self.logger.log_box(f"CHECK FAILED: {task_name}")
        for message in log_messages:
            self.logger.error(message)

    def _get_revert_method(self, task_name: str):
        """Get revert method for a task if it exists."""
        if task_name.startswith("set_"):
            revert_method_name = f"revert_{task_name[4:]}"
            return getattr(self, revert_method_name, None)
        return None

    def _is_success(self, result) -> bool:
        """Check if a task result indicates success."""
        return result[0] if isinstance(result, (tuple, list)) else bool(result)

    def _get_log_messages(self, result) -> list:
        """Extract log messages from a task result."""
        return (
            result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else []
        )

    def _revert_states(self, original_states: Dict[str, Any]) -> None:
        """Revert all stored states."""
        if not original_states:
            self.logger.debug("No states to revert.")
            return

        self.logger.info("Reverting temporary states...")

        # Revert in reverse order (LIFO)
        for task_name, state_info in reversed(original_states.items()):
            revert_method = state_info["revert_method"]
            original_value = state_info["original_value"]

            try:
                revert_method(original_value)
                self.logger.debug(f"Reverted {task_name} to: {original_value}")
            except Exception as e:
                self.logger.error(f"Error reverting {task_name}: {e}")

        self.logger.info("State reversion completed.")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
