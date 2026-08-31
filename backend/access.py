"""Compatibility exports for the database-backed permission policy."""

from backend.permissions import (
    accessible_project_filter,
    accessible_project_or_404,
    project_access,
    require_project_permission,
)

__all__ = [
    "accessible_project_filter",
    "accessible_project_or_404",
    "project_access",
    "require_project_permission",
]
