from dataclasses import dataclass
from typing import Callable, Optional

from pydantic import BaseModel, Field


@dataclass
class Snapshot:
    up: Optional[bool]
    p95_s: Optional[float]
    disk_ratio: Optional[float]
    source: str  # "prometheus" or "probe" (degraded mode)


@dataclass
class Incident:
    type: str       # service_down | high_latency | disk_full
    severity: int   # 1 = most severe
    evidence: str


@dataclass
class ActionResult:
    ok: bool
    detail: str
    undo: Optional[Callable[[], "ActionResult"]] = None


class Decision(BaseModel):
    """What the diagnoser proposes. Validated, then checked again by the policy."""
    action: str
    reason: str
    confidence: float = Field(ge=0, le=1)
