# !/usr/bin/python
# coding=utf-8
import contextlib
from inspect import signature
from typing import Dict, Any


class TaskFactory:
    """A factory class for managing and executing tasks in a scene export pipeline."""

    def __init__(self, logger):
        self.logger = logger

    @contextlib.contextmanager
    def _manage_context(self, tasks: Dict[str, Any]) -> Dict[str, Any]:
        """Manage task states by setting them once and reverting after, returning task results."""
        original_states = {}
        task_results = {}
        self.logger.info(f"Running {len(tasks)} tasks")

        for index, (task_name, value) in enumerate(tasks.items(), start=1):
            method = getattr(self, task_name, None)
            if method:
                self.logger.debug(f"Executing Task #{index}/{len(tasks)}: {task_name}")

                # Handle state changes and reversions
                revert_method_name = (
                    f"revert_{task_name[4:]}" if task_name.startswith("set_") else None
                )
                revert_method = (
                    getattr(self, revert_method_name, None)
                    if revert_method_name
                    else None
                )

                try:  # Execute the task and log results centrally
                    result = self._execute_task_method(method, value)
                    task_results[task_name] = result

                    # Unpack the result if it's a tuple (bool, list)
                    if isinstance(result, (tuple, list)):
                        success, log_messages = result
                    else:
                        success, log_messages = result, []

                    # If the check failed, log the failure messages
                    if task_name.startswith("check_") and not success:
                        self._log_check_failed(task_name, log_messages)

                    # Store revert info if applicable
                    if revert_method:
                        original_states[revert_method_name] = result

                except Exception as e:
                    self.logger.error(f"Error during task {task_name}: {e}")
                    raise
            else:
                self.logger.warning(f"Task {task_name} not found. Skipping.")

        yield task_results

        # Revert any changes made during tasks
        for revert_method_name, original_value in reversed(original_states.items()):
            revert_method = getattr(self, revert_method_name, None)
            if revert_method:
                try:
                    revert_method(original_value)
                    self.logger.debug(f"Reverted {revert_method_name}")
                except Exception as e:
                    self.logger.error(f"Error reverting {revert_method_name}: {e}")

        self.logger.info("Task execution completed, states reverted.")

    def _execute_task_method(self, method, value: Any):
        try:  # Check how many parameters the method actually accepts
            sig = signature(method)
            if len(sig.parameters) == 0:
                return method()
            return method(value)
        except Exception as e:
            self.logger.error(f"Error executing task method {method.__name__}: {e}")
            raise

    def run_tasks(self, tasks: Dict[str, Any]) -> bool:
        """Run tasks and checks, returning True if all pass, False if any checks fail."""
        if not tasks:
            self.logger.notice("No tasks provided to run.")
            return True

        all_checks_passed = True
        failed_checks = []
        task_list = list(tasks.items())

        with self._manage_context(tasks) as task_results:
            for index, (task_name, result) in enumerate(task_results.items(), start=1):
                progress = f"[{index}/{len(task_list)}]"

                if task_name.startswith("check_"):
                    if isinstance(result, tuple):
                        success, _ = result
                    else:
                        success = result

                    if not success:
                        failed_checks.append(task_name)
                        all_checks_passed = False
                    else:
                        self.logger.success(f"{progress} Check passed: {task_name}")
                else:
                    self.logger.result(f"{progress} Task completed: {task_name}")

        if not all_checks_passed:
            self.logger.log_box(
                "SUMMARY OF FAILED CHECKS",
                [f"- {check}" for check in failed_checks],
            )
            self.logger.error("Export aborted due to failed checks.")
        else:
            self.logger.success("All tasks and checks completed successfully.")

        return all_checks_passed

    def _log_check_failed(self, task_name: str, log_messages: list):
        """Log the 'CHECK FAILED' box after task fails."""
        self.logger.log_box(f"CHECK FAILED: {task_name}")
        for message in log_messages:
            self.logger.error(message)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
