"""Rule-based asset validation: registry, runner, and rule implementations."""

from __future__ import annotations

# Imported for their side effect of registering rules with @register on import.
from pixel_forge.validation import (
    rules_animation,  # noqa: F401
    rules_pixel,  # noqa: F401
    rules_tileset,  # noqa: F401
)
from pixel_forge.validation.engine import (
    Rule,
    RuleContext,
    RuleMeta,
    register,
    registered_rules,
    run_validation,
)

__all__ = [
    "Rule",
    "RuleContext",
    "RuleMeta",
    "register",
    "registered_rules",
    "run_validation",
]
