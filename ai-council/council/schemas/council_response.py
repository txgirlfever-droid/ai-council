"""
Council Packet and CouncilResponse structured schema.
Correction #10: Structured JSON contract + fallback audit.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class AgreementItem(BaseModel):
    agent_id: str
    point: str


class DisagreementItem(BaseModel):
    agent_id: str
    point: str
    reason: str


class RiskItem(BaseModel):
    title: str
    likelihood: str  # LOW | MEDIUM | HIGH
    impact: str  # LOW | MEDIUM | HIGH | CRITICAL
    description: str


class CouncilResponse(BaseModel):
    """
    Structured response contract all agents must return.
    Correction #10: Parsed from agent output; fallback to prose on failure.
    """

    summary: str = Field(..., description="1-3 sentence position summary")
    position: str = Field(..., description="OPTION_A | OPTION_B | ALTERNATIVE | NEUTRAL")
    recommendation: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    agreements: list[AgreementItem] = Field(default_factory=list)
    disagreements: list[DisagreementItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    raw_notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def check_position(cls, data: Any) -> Any:
        valid = {"OPTION_A", "OPTION_B", "ALTERNATIVE", "NEUTRAL"}
        pos = data.get("position", "")
        if pos not in valid:
            data["position"] = "NEUTRAL"
        return data


def parse_council_response(raw: str) -> tuple[CouncilResponse | None, bool]:
    """
    Returns (parsed_response, is_valid).
    On failure returns (None, False) — caller stores raw for audit.
    """
    import json
    import re

    # Extract JSON block from markdown code fences or raw
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = json_match.group(1) if json_match else raw.strip()

    try:
        data = json.loads(json_str)
        return CouncilResponse.model_validate(data), True
    except Exception:
        return None, False
