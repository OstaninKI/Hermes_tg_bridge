"""Hermes platform plugin: live Telegram user-account DM gateway.

Hermes loads directory plugins as ``hermes_plugins.<slug>`` packages via
``spec_from_file_location`` (hermes_cli/plugins.py:_load_directory_module), so the
sibling module must be imported relatively — an absolute ``from adapter import ...``
is not resolvable from that package context.
"""

from .adapter import register

__all__ = ["register"]
