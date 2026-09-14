"""Platform-agnostic policy: models, interfaces, membership and sampling logic.

Nothing in this package may import from ``treehawk.platforms``; the dependency
points the other way (Dependency Inversion). Concrete implementations are
injected by the composition root in :mod:`treehawk.cli`.
"""
