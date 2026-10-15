"""ModelRouter: pick a model per role from the catalog.

Policies are explicit so the user can reason about them in the settings UI:

- ``local_first``      — use the GPU you already paid for; fall back to cloud
- ``cloud_first``      — best quality now, local as fallback
- ``cheapest_capable`` — cheapest model that meets the requirement
- ``privacy_lock``     — local only, never sends data off the machine
- ``fixed``            — exactly the model the user chose

Probed models outrank declared ones: a model that *proved* it can call tools
beats one whose server merely claims it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openagent.core.logging import logger
from openagent.provider.catalog import ModelCatalog
from openagent.provider.model_ref import Capabilities, ModelRef

ROLES = ("main", "subagent", "compact", "title", "verify")


class RoutingPolicy(StrEnum):
    FIXED = "fixed"
    LOCAL_FIRST = "local_first"
    CLOUD_FIRST = "cloud_first"
    CHEAPEST_CAPABLE = "cheapest_capable"
    PRIVACY_LOCK = "privacy_lock"


class NoCapableModel(RuntimeError):
    def __init__(self, requirement: Capabilities, policy: RoutingPolicy, available: list[ModelRef]) -> None:
        self.requirement = requirement
        self.policy = policy
        self.available = available
        missing = []
        if requirement.tools:
            missing.append("tool calling")
        if requirement.vision:
            missing.append("image input")
        if requirement.reasoning:
            missing.append("extended reasoning")
        if requirement.json_mode:
            missing.append("JSON mode")
        detail = f" (needs {', '.join(missing)})" if missing else ""
        known = ", ".join(r.key for r in available[:8]) or "none"
        super().__init__(
            f"No model under policy '{policy}'{detail} "
            f"(context >= {requirement.context_window}). Known models: {known}"
        )


@dataclass
class RoutingRules:
    policy: RoutingPolicy = RoutingPolicy.LOCAL_FIRST
    default_key: str = ""
    roles: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"policy": str(self.policy), "default_key": self.default_key, "roles": dict(self.roles)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingRules:
        return cls(
            policy=RoutingPolicy(data.get("policy", RoutingPolicy.LOCAL_FIRST)),
            default_key=data.get("default_key", ""),
            roles=dict(data.get("roles") or {}),
        )


class ModelRouter:
    def __init__(self, catalog: ModelCatalog, rules: RoutingRules | None = None) -> None:
        self.catalog = catalog
        self.rules = rules or RoutingRules()

    # ----------------------------------------------------------------- select

    def select(
        self,
        requirement: Capabilities | None = None,
        role: str | None = None,
    ) -> ModelRef:
        """Resolve the model for ``role``, honouring the policy."""
        requirement = requirement or Capabilities()

        explicit = self.rules.roles.get(role) if role else None
        for key in (explicit, self.rules.default_key):
            if key:
                ref = self.catalog.get(key)
                if ref is not None:
                    if ref.caps.supports(requirement):
                        return ref
                    logger.warning(
                        f"Model '{key}' cannot satisfy the requirement for role "
                        f"'{role}'; falling back to policy selection"
                    )

        candidates = self.candidates(requirement)
        if not candidates:
            raise NoCapableModel(requirement, self.rules.policy, self.catalog.all_models())
        return candidates[0]

    def candidates(self, requirement: Capabilities) -> list[ModelRef]:
        """Every capable model, best first."""
        pool = self.catalog.all_models()
        capable = [ref for ref in pool if ref.caps.supports(requirement)]

        if self.rules.policy is RoutingPolicy.PRIVACY_LOCK:
            capable = [ref for ref in capable if ref.is_local]

        policy = self.rules.policy

        def rank(ref: ModelRef) -> tuple:
            latency = self.catalog.probe_result(ref)
            measured = latency.latency_ms if latency and latency.ok else 1e9
            if policy is RoutingPolicy.CHEAPEST_CAPABLE:
                primary = ref.cost.marginal_cost_per_turn
            elif policy is RoutingPolicy.LOCAL_FIRST:
                primary = 0 if ref.is_local else 1
            elif policy is RoutingPolicy.CLOUD_FIRST:
                primary = 0 if not ref.is_local else 1
            else:  # FIXED / unranked: keep the user's default at the front
                primary = 0 if ref.key == self.rules.default_key else 1
            # Facts beat claims, then policy, then headroom, then measured latency.
            return (not ref.caps.probed, primary, -ref.caps.context_window, measured)

        return sorted(capable, key=rank)
