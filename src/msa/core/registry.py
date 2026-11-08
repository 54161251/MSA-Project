"""Extension points. Each role's `plugin.py` registers into this at import time.

This is how parts merge without editing each other's files: R1 never imports an agent
directly; it discovers `src/msa/agents/*/plugin.py` and `src/msa/safety_tools/plugin.py`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

# (db, approval, staff_user) -> None
ApprovalHandler = Callable[[Any, Any, Any], None]
# (db, payload: dict, actor_id: str) -> AgentResult
AgentFn = Callable[[Any, dict, str], Any]


@dataclass
class Seeder:
    order: int
    name: str
    fn: Callable[..., None]  # fn(db, approve_all: bool)


@dataclass
class Registry:
    routers: list[APIRouter] = field(default_factory=list)
    approve_handlers: dict[str, ApprovalHandler] = field(default_factory=dict)
    reject_handlers: dict[str, ApprovalHandler] = field(default_factory=dict)
    event_handlers: dict[str, list[Callable[..., None]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    seeders: list[Seeder] = field(default_factory=list)
    daily_jobs: dict[str, Callable[[Any], dict]] = field(default_factory=dict)
    agents: dict[str, AgentFn] = field(default_factory=dict)
    briefing_sections: dict[str, Callable[[Any], dict]] = field(default_factory=dict)

    # -- registration helpers (used as decorators) --

    def router(self, r: APIRouter) -> APIRouter:
        if r not in self.routers:
            self.routers.append(r)
        return r

    def on_approve(self, kind: str):
        def deco(fn: ApprovalHandler) -> ApprovalHandler:
            self.approve_handlers[kind] = fn
            return fn

        return deco

    def on_reject(self, kind: str):
        def deco(fn: ApprovalHandler) -> ApprovalHandler:
            self.reject_handlers[kind] = fn
            return fn

        return deco

    def on_event(self, name: str):
        def deco(fn):
            if fn not in self.event_handlers[name]:
                self.event_handlers[name].append(fn)
            return fn

        return deco

    def seeder(self, order: int, name: str):
        def deco(fn):
            if not any(s.name == name for s in self.seeders):
                self.seeders.append(Seeder(order, name, fn))
            return fn

        return deco

    def daily_job(self, name: str):
        def deco(fn):
            self.daily_jobs[name] = fn
            return fn

        return deco

    def agent(self, name: str):
        def deco(fn: AgentFn) -> AgentFn:
            self.agents[name] = fn
            return fn

        return deco

    def briefing_section(self, name: str):
        def deco(fn):
            self.briefing_sections[name] = fn
            return fn

        return deco


registry = Registry()
