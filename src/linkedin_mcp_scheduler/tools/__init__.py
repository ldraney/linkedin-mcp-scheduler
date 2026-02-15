"""Tool registration — imports every tool module so @mcp.tool() decorators fire."""

from __future__ import annotations


def register_all_tools() -> None:
    """Import all tool modules, which register tools via module-level @mcp.tool() decorators."""
    from . import scheduling  # noqa: F401
