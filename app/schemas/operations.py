"""Operational HTTP response contracts."""

from typing import Literal

from pydantic import BaseModel


class ReadinessView(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, Literal["ready", "unavailable"]]
