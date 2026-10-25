"""Plugin-based job source system for ApplyQueue (inherited from LangHire)."""
from .registry import PluginRegistry, PluginConfig

__all__ = ["PluginRegistry", "PluginConfig"]
