"""Runtime and static guards for dashboard import boundaries."""

from __future__ import annotations

import ast
from typing import Iterable

FORBIDDEN_MODULE_PREFIXES: tuple[str, ...] = (
    "kiteconnect",
    "broker.zerodha",
    "risk.risk_engine",
    "execution.order_manager",
    "strategy.strategy_evaluation_engine",
    "apme.adaptive_position_management_engine",
)

ALLOWED_MODULE_PREFIXES: tuple[str, ...] = (
    "dashboard",
    "streamlit",
    "pandas",
    "plotly",
    "system.integration_engine",
    "config.application_configuration",
)


class ForbiddenDashboardImportError(ImportError):
    """Raised when a dashboard module imports a forbidden backend module."""


def assert_no_forbidden_dashboard_imports(module_ast: ast.AST) -> None:
    """Assert that an AST contains no forbidden dashboard imports.

    Args:
        module_ast: Parsed module abstract syntax tree.

    Raises:
        ForbiddenDashboardImportError: If a forbidden import is detected.
    """
    for node in ast.walk(module_ast):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _raise_if_forbidden(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                _raise_if_forbidden(node.module)


def _raise_if_forbidden(module_name: str) -> None:
    """Raise when module name matches a forbidden prefix."""
    for prefix in FORBIDDEN_MODULE_PREFIXES:
        if module_name == prefix or module_name.startswith(f"{prefix}."):
            raise ForbiddenDashboardImportError(
                f"Forbidden dashboard import detected: {module_name}"
            )


def is_allowed_dashboard_import(module_name: str) -> bool:
    """Return whether a module name is on the dashboard allowlist.

    Args:
        module_name: Fully qualified module name.

    Returns:
        ``True`` when the module prefix is explicitly allowlisted.
    """
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in ALLOWED_MODULE_PREFIXES
    )


def collect_forbidden_imports(module_ast: ast.AST) -> tuple[str, ...]:
    """Collect forbidden import module names from an AST.

    Args:
        module_ast: Parsed module abstract syntax tree.

    Returns:
        Tuple of forbidden module names found in the AST.
    """
    found: list[str] = []
    for node in ast.walk(module_ast):
        names: Iterable[str]
        if isinstance(node, ast.Import):
            names = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = (node.module,)
        else:
            continue
        for name in names:
            for prefix in FORBIDDEN_MODULE_PREFIXES:
                if name == prefix or name.startswith(f"{prefix}."):
                    found.append(name)
    return tuple(sorted(set(found)))
