"""Task/TODO manager for multi-step workflow tracking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """Possible statuses for a task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELETED = "deleted"


@dataclass
class TodoTask:
    """Represents a single task in the todo list."""

    id: str
    subject: str
    description: str = ""
    active_form: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=lambda: __import__("time").time())
    updated_at: float = field(default_factory=lambda: __import__("time").time())

    def to_dict(self) -> dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "activeForm": self.active_form,
            "status": self.status.value,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class TaskManager:
    """Manages a list of tasks for tracking multi-step work.

    This manager provides CRUD operations for tasks and maintains state
    across agent turns for workflow tracking.
    """

    def __init__(self):
        self.tasks: dict[str, TodoTask] = {}
        self._next_id_counter = 0

    def _generate_id(self) -> str:
        """Generate a unique task ID."""
        self._next_id_counter += 1
        return f"task_{uuid.uuid4().hex[:8]}"

    def create_task(
        self,
        subject: str,
        description: str = "",
        active_form: str = "",
    ) -> str:
        """Create a new task.

        Args:
            subject: Task title/subject (required)
            description: Detailed description
            active_form: Present continuous form for display

        Returns:
            Task ID
        """
        task = TodoTask(
            id=self._generate_id(),
            subject=subject,
            description=description,
            active_form=active_form,
        )
        self.tasks[task.id] = task
        return task.id

    def create_tasks(self, tasks: list[dict[str, str]]) -> list[str]:
        """Create multiple tasks at once.

        Args:
            tasks: List of task dictionaries with keys: subject (required), description, activeForm

        Returns:
            List of created task IDs
        """
        ids = []
        for task_data in tasks:
            task_id = self.create_task(
                subject=task_data.get("subject", ""),
                description=task_data.get("description", ""),
                active_form=task_data.get("activeForm", ""),
            )
            ids.append(task_id)
        return ids

    def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
    ) -> bool:
        """Update an existing task.

        Args:
            task_id: ID of the task to update
            status: New status (pending, in_progress, completed, deleted)
            subject: New subject/title for the task
            description: New description for the task
            active_form: New active form for display

        Returns:
            True if updated, False if task not found
        """
        task = self.tasks.get(task_id)
        if not task:
            return False

        if status is not None:
            task.status = status
        if subject is not None:
            task.subject = subject
        if description is not None:
            task.description = description
        if active_form is not None:
            task.active_form = active_form

        task.updated_at = __import__("time").time()
        return True

    def get_task(self, task_id: str) -> TodoTask | None:
        """Get a task by ID.

        Args:
            task_id: Task ID to retrieve

        Returns:
            Task if found, None otherwise
        """
        return self.tasks.get(task_id)

    def list_tasks(
        self,
        status_filter: TaskStatus | None = None,
        include_deleted: bool = False,
    ) -> list[TodoTask]:
        """Get the current list of tasks.

        Args:
            status_filter: Optional filter by status
            include_deleted: Whether to include deleted tasks

        Returns:
            List of matching tasks
        """
        result = []
        for task in self.tasks.values():
            if not include_deleted and task.status == TaskStatus.DELETED:
                continue
            if status_filter is not None and task.status != status_filter:
                continue
            result.append(task)

        # Sort by creation time (newest first)
        result.sort(key=lambda t: t.created_at, reverse=True)
        return result

    def delete_task(self, task_id: str) -> bool:
        """Mark a task as deleted.

        Args:
            task_id: Task ID to delete

        Returns:
            True if deleted, False if not found
        """
        task = self.tasks.get(task_id)
        if not task:
            return False

        task.status = TaskStatus.DELETED
        task.updated_at = __import__("time").time()
        return True

    def get_summary(self) -> str:
        """Get a formatted summary of all tasks.

        Returns:
            Formatted string with task list and statuses
        """
        active_tasks = self.list_tasks(include_deleted=False)

        if not active_tasks:
            return "No active tasks."

        lines = ["=== Task List ==="]

        # Group by status
        by_status: dict[TaskStatus, list[TodoTask]] = {
            s: [] for s in TaskStatus
        }
        for task in active_tasks:
            by_status[task.status].append(task)

        for status in [TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.COMPLETED]:
            tasks = by_status[status]
            if not tasks:
                continue

            status_icon = {
                TaskStatus.IN_PROGRESS: "[INPROG]",
                TaskStatus.PENDING: "[PENDING]",
                TaskStatus.COMPLETED: "[DONE]",
            }.get(status, "[?]")

            lines.append(f"\n{status_icon} {status.value.upper()}:")

            for task in tasks:
                lines.append(f"  • [{task.id}] {task.subject}")
                if task.description:
                    lines.append(f"    {task.description[:100]}{'...' if len(task.description) > 100 else ''}")

        return "\n".join(lines)


# Global task manager instance for use by tools
_global_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    """Get or create the global task manager."""
    global _global_task_manager
    if _global_task_manager is None:
        _global_task_manager = TaskManager()
    return _global_task_manager


async def reset_task_manager():
    """Reset the global task manager (useful for testing)."""
    global _global_task_manager
    _global_task_manager = None
