from __future__ import annotations

"""AI Strategy Copilot for DrawWise.

The mathematical engine remains authoritative for number generation.  The copilot
reviews an already-generated portfolio, highlights structural strengths/weaknesses,
and can recommend whether to keep it or rerun under a different objective.

When an OpenAI API key is available, the optional cloud reviewer uses the Responses
API.  The API key is never written by this module; the desktop UI keeps a pasted key
in memory for the current session only, or reads OPENAI_API_KEY from the environment.
"""

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import error, request


DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_ENDPOINT = "https://api.openai.com/v1/responses"


@dataclass(frozen=True)
class CopilotReview:
    source: str
    model: str | None
    text: str


def _ticket_dict(ticket) -> dict[str, Any]:
    return {
        "main": list(ticket.main),
        "special": list(ticket.special),
    }


def portfolio_payload(result) -> dict[str, Any]:
    """Return a compact, non-personal payload for an AI review."""
    intel = result.intelligence
    payload: dict[str, Any] = {
        "game": result.config.name,
        "line_count": len(result.tickets),
        "tickets": [_ticket_dict(ticket) for ticket in result.tickets],
    }
    if intel is None:
        payload["intelligence"] = None
        return payload

    payload["intelligence"] = {
        "objective": intel.objective,
        "portfolio_rating": round(intel.portfolio_rating, 3),
        "pair_coverage_efficiency": round(intel.pair_coverage, 6),
        "triple_coverage_efficiency": round(intel.triple_coverage, 6),
        "average_main_overlap": round(intel.avg_overlap, 6),
        "maximum_main_overlap": intel.max_overlap,
        "crowd_risk": round(intel.crowd_risk, 6),
        "history_weight": round(intel.history_gate.history_weight, 6),
        "history_verdict": intel.history_gate.verdict,
        "random_challenge_percentile": round(intel.random_percentile, 3),
        "target_label": intel.target_label,
        "target_probability": round(intel.target_probability, 10),
        "random_median_probability": round(intel.random_median_probability, 10),
        "jackpot_odds": intel.jackpot_odds,
        "candidates_evaluated": intel.candidates_evaluated,
        "search_moves": intel.search_moves,
        "challenge_draws": intel.challenge_draws,
    }
    return payload


def local_strategy_review(result) -> CopilotReview:
    """Explain the portfolio using deterministic, auditable rules.

    This is deliberately labelled local analysis rather than AI.  It guarantees that
    DrawWise still provides useful side-by-side guidance with no internet connection
    and no API key.
    """
    intel = result.intelligence
    if intel is None:
        return CopilotReview(
            source="local",
            model=None,
            text=(
                "Generate a Maximum Intelligence portfolio first. The Strategy Copilot "
                "will then review coverage, overlap, crowd risk and the random challenge."
            ),
        )

    strengths: list[str] = []
    cautions: list[str] = []

    if intel.random_percentile >= 75:
        strengths.append(f"random challenge is strong ({intel.random_percentile:.0f}th percentile)")
    elif intel.random_percentile < 50:
        cautions.append(f"random challenge is below median ({intel.random_percentile:.0f}th percentile)")
    else:
        strengths.append(f"random challenge is competitive ({intel.random_percentile:.0f}th percentile)")

    if intel.pair_coverage >= 0.93:
        strengths.append("pair coverage is highly efficient")
    elif intel.pair_coverage < 0.82:
        cautions.append("pair coverage has avoidable duplication")

    if intel.triple_coverage >= 0.96:
        strengths.append("triple coverage is highly efficient")
    elif intel.triple_coverage < 0.86:
        cautions.append("triple coverage can be improved")

    if intel.max_overlap >= max(3, result.config.main_pick - 2):
        cautions.append(f"one ticket pair overlaps by {intel.max_overlap} main numbers")
    elif intel.avg_overlap <= 1.25:
        strengths.append(f"average overlap is controlled ({intel.avg_overlap:.2f})")

    if intel.crowd_risk >= 1.2:
        cautions.append("human-selection / prize-sharing risk is elevated")
    elif intel.crowd_risk <= 0.45:
        strengths.append("crowd-pattern risk is low")

    if intel.history_gate.history_weight <= 0.04:
        strengths.append("weak historical signal is correctly kept near zero")

    if intel.random_percentile < 50:
        action = "REGENERATE"
        objective_hint = (
            "Try ‘Maximise 3+ coverage’" if intel.objective != "Maximise 3+ coverage"
            else "Regenerate the same objective and keep the stronger challenge result"
        )
    elif intel.crowd_risk >= 1.2 and intel.objective != "Minimise prize sharing":
        action = "CONSIDER AN ALTERNATIVE"
        objective_hint = "Try ‘Minimise prize sharing’ if payout-sharing risk matters most"
    else:
        action = "KEEP"
        objective_hint = "The current objective is structurally sound"

    strength_text = "; ".join(strengths) if strengths else "no exceptional structural advantage was detected"
    caution_text = "; ".join(cautions) if cautions else "no material structural weakness was detected"

    text = (
        f"LOCAL STRATEGY REVIEW — {action}\n\n"
        f"Strongest signals: {strength_text}.\n\n"
        f"Watch: {caution_text}.\n\n"
        f"Next move: {objective_hint}.\n\n"
        f"The top-prize chance remains {intel.jackpot_odds}. This review does not predict the next draw; "
        "it audits how efficiently your chosen ticket budget is structured."
    )
    return CopilotReview(source="local", model=None, text=text)


def _extract_output_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def openai_strategy_review(
    result,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 35.0,
) -> CopilotReview:
    """Ask OpenAI to critique a mathematically generated portfolio.

    No historical personal data or account information is sent.  Only the game,
    generated tickets and DrawWise's portfolio metrics are included.
    """
    key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("No OpenAI API key is configured.")

    chosen_model = (model or os.environ.get("DRAWWISE_AI_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    endpoint = os.environ.get("OPENAI_RESPONSES_URL", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    payload = portfolio_payload(result)

    instructions = (
        "You are the DrawWise AI Strategy Copilot. The mathematical engine has already selected the tickets. "
        "You are a skeptical portfolio critic, not a lottery predictor. Use only the supplied metrics. "
        "Never claim that hot/cold numbers, AI, physics or historical patterns can predict an independent fair draw. "
        "Do not invent probabilities. Do not replace the ticket numbers. "
        "Return a concise review with: (1) KEEP / REGENERATE / CONSIDER ALTERNATIVE, "
        "(2) strongest portfolio property, (3) weakest property, and (4) one objective recommendation. "
        "Explain that jackpot odds are unchanged except by buying more distinct lines."
    )

    body = json.dumps(
        {
            "model": chosen_model,
            "reasoning": {"effort": "low"},
            "instructions": instructions,
            "input": "Review this DrawWise portfolio:\n" + json.dumps(payload, separators=(",", ":")),
            "max_output_tokens": 500,
        }
    ).encode("utf-8")

    req = request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"OpenAI review failed (HTTP {exc.code}): {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"OpenAI review could not connect: {exc.reason}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("OpenAI returned an unreadable response.") from exc

    text = _extract_output_text(data)
    if not text:
        raise RuntimeError("OpenAI returned no review text.")
    return CopilotReview(source="openai", model=chosen_model, text=text)
