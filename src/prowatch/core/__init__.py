"""Platform-agnostic policy: models, interfaces, membership and sampling logic.

Nothing in this package may import from ``prowatch.platforms``; the dependency
points the other way (Dependency Inversion). Concrete implementations are
injected by the composition root in :mod:`prowatch.cli`.
"""
