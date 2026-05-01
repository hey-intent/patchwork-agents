from __future__ import annotations

from .base import SourceProvider
from .github import GitHubProvider

_REGISTRY: dict[str, type[SourceProvider]] = {
    "github": GitHubProvider,
}


def get_provider(name: str, **kwargs) -> SourceProvider:
    if name not in _REGISTRY:
        raise ValueError(f"unknown provider {name!r}, available: {list(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def register_provider(name: str, cls: type[SourceProvider]) -> None:
    """Allow external providers to plug in without core changes."""
    _REGISTRY[name] = cls
