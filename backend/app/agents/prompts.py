"""User-editable agent prompts.

Prompts live as Markdown files in backend/prompts/ (Bull.md, Bear.md, Judge.md) so they
can be tuned without touching code. Files are re-read on every run — edit and re-run, no
restart needed. If a file is missing or unreadable, the embedded default is used; if a
custom file is missing a required placeholder, it is rejected (defaults used) so a typo
can't silently produce a data-less prompt."""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import BACKEND_DIR

log = logging.getLogger("pvt.prompts")

PROMPTS_DIR = BACKEND_DIR / "prompts"

REQUIRED_PLACEHOLDERS: dict[str, set[str]] = {
    "Bull": {"{sym}", "{data}"},
    "Bear": {"{sym}", "{data}"},
    "Judge": {"{sym}", "{bull_case}", "{bear_case}"},
}

DEFAULTS: dict[str, str] = {
    "Bull": """You are the BULL agent — a disciplined long-side analyst. Build the STRONGEST
honest case to BUY {sym} right now, using ONLY the data below. Cite exact numbers.
If the data is genuinely weak, say so with a low signal_strength — never invent strength.
Strategy context: style={style}, horizon={horizon}.
Data: {data}""",
    "Bear": """You are the BEAR agent — a skeptical short-side analyst. Build the STRONGEST
honest case to SELL / avoid {sym} right now, using ONLY the data below. Cite exact numbers.
Hunt for stretched valuation, deteriorating fundamentals, broken momentum, crowding. If the
data is genuinely solid, admit it with a low signal_strength — never invent weakness.
Strategy context: style={style}, horizon={horizon}.
Data: {data}""",
    "Judge": """You are the JUDGE agent — an impartial investment committee chair. The Bull and the
Bear have each argued their case on {sym}. Weigh BOTH arguments against the underlying data
and deliver a consolidated recommendation. Penalize claims the data does not back; do not
split the difference by default; action is "hold" when evidence is balanced or both cases
are weak. Name the decisive numbers in your rationale.
Strategy context: style={style}, horizon={horizon}.
BULL case (signal {bull_strength}/100): {bull_case}
Bull key points: {bull_points}
BEAR case (signal {bear_strength}/100): {bear_case}
Bear key points: {bear_points}
Underlying data: {data}""",
}


def load_prompt(name: str) -> str:
    """Return the prompt template for Bull / Bear / Judge (file first, default fallback)."""
    path = PROMPTS_DIR / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULTS[name]
    if not text:
        return DEFAULTS[name]
    missing = [p for p in REQUIRED_PLACEHOLDERS[name] if p not in text]
    if missing:
        log.warning("%s ignored — missing required placeholders %s; using default", path.name, missing)
        return DEFAULTS[name]
    return text


def render(template: str, **kwargs) -> str:
    """Replace {key} tokens by simple substitution. Unlike str.format, this never
    chokes on stray braces in rich Markdown prompts (tables, code blocks, {TICKER}
    examples) and leaves unknown tokens intact."""
    out = template
    for key, value in kwargs.items():
        out = out.replace("{" + key + "}", str(value))
    return out
