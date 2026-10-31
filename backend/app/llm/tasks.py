"""The model tasks the app uses. The test set in evals/ calls these same functions.

Each task keeps its input small: compact JSON, at most a few remembered "Memory:" lines,
and an output cap. Simple instructions are handled by code first (fast_paths) at no token cost.
"""
import re
from typing import Literal

from pydantic import Field, create_model

from .. import config
from ..schemas import DrawingReadOut, InstructionOut, OpenSpaceOut, ParseOrderOut, SpaceSuggestion
from . import client, drawing_fixes, fast_paths, prompts, usage
from .digest import compact, for_model


def _with_memory(text: str, memory_lines: list[str] | None) -> str:
    if not memory_lines:
        return text
    return text + "\nMemory:\n" + "\n".join(f"- {m}" for m in memory_lines)


def read_order(text: str, model: str | None = None, memory_lines: list[str] | None = None) -> ParseOrderOut:
    return client.chat_model(prompts.PARSE_ORDER_SYSTEM, _with_memory(text.strip(), memory_lines), ParseOrderOut,
                             model=model, max_tokens=config.MAX_TOKENS_JSON)


def read_drawing(text: str | None, image: str | None = None, segments: list | None = None,
                 model: str | None = None) -> DrawingReadOut:
    user = text.strip() if text and text.strip() else "Read the attached drawing."
    if segments:
        user += "\nSegmentation boxes (0-1, x0,y0,x1,y1): " + compact(segments)
    out = client.chat_model(prompts.READ_DRAWING_SYSTEM, user, DrawingReadOut, image=image,
                            model=model or config.READER_MODEL_NAME, max_tokens=config.MAX_TOKENS_DRAWING,
                            effort=config.REASONING_EFFORT_DRAWING)
    # Rule-based repairs for real drawings (LOA vs LBP, misread digits, duplicate labels); all reported.
    layout, checks, fixes, estimated = drawing_fixes.normalise(out.layout)
    return DrawingReadOut(layout=layout, notes=drawing_fixes.filter_ai_notes(out.notes, fixes), checks=checks,
                          fixes=fixes, estimated=estimated)


def read_instruction(instruction: str, cargo_tanks: list[str], grades: list[str], current: dict,
                     model: str | None = None, memory_lines: list[str] | None = None,
                     aliases: dict[str, str] | None = None) -> InstructionOut:
    quick = fast_paths.try_instruction(instruction, cargo_tanks, grades, current, aliases)
    if quick is not None:
        usage.record(skipped=True)
        return quick
    context = {"instruction": instruction, "cargo_tanks": cargo_tanks, "grades": grades, "current": current}
    return client.chat_model(prompts.STOWAGE_INSTRUCTIONS_SYSTEM, _with_memory(compact(context), memory_lines),
                             InstructionOut, model=model, max_tokens=config.MAX_TOKENS_JSON)


def open_space(facts: dict, tank_ids: list[str], model: str | None = None) -> OpenSpaceOut:
    """The AI chooses what else fits on a ship's current voyage: grade, tanks and quantity, checking each itself.

    The reply schema only accepts the ship's real tank ids, so an invented tank is sent back for correction.
    """
    tank = Literal[tuple(tank_ids)]  # type: ignore[valid-type]
    suggestion = create_model("ShipSpaceSuggestion", __base__=SpaceSuggestion, tanks=(list[tank], Field(min_length=1)))
    schema = create_model("ShipOpenSpaceOut", __base__=OpenSpaceOut, suggestions=(list[suggestion], []))
    return client.chat_model(prompts.OPEN_SPACE_SYSTEM, compact(facts), schema, model=model,
                             max_tokens=config.MAX_TOKENS_OPEN_SPACE, effort=config.REASONING_EFFORT_OPEN_SPACE)


def cleaning_checklist(digest: dict) -> str:
    return client.chat_text(prompts.CLEANING_SYSTEM, compact(digest), max_tokens=config.MAX_TOKENS_CHECKLIST)


def _check_explanation(text: str) -> None:
    """Reject a reply that is cut off or missing its parts, so callers fall back to the built-in explanation."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    last = re.sub(r"^[-*\u2022]\s*", "", lines[-1]) if lines else ""
    if "Why:" not in text or "What to do:" not in text or not last.endswith((".", "!", "?", ")")):
        raise client.ModelError("Incomplete explanation")


def explain(system: str, digest: dict, memory_lines: list[str] | None = None, question: str | None = None) -> str:
    user = compact(for_model(digest)) +(f"\nQuestion: {question.strip()}" if question else "")
    return client.chat_text(system, _with_memory(user, memory_lines), max_tokens=config.MAX_TOKENS_EXPLAIN,
                            validate=_check_explanation)
