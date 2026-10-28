"""The model tasks the app uses. The test set in evals/ calls these same functions.

Each task keeps its input small: compact JSON, at most a few remembered "Memory:" lines,
and an output cap. Simple instructions are handled by code first (fast_paths) at no token cost.
"""
from .. import config
from ..schemas import DrawingReadOut, InstructionOut, ParseOrderOut
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
                     model: str | None = None, memory_lines: list[str] | None = None) -> InstructionOut:
    quick = fast_paths.try_instruction(instruction, cargo_tanks, grades, current)
    if quick is not None:
        usage.record(skipped=True)
        return quick
    context = {"instruction": instruction, "cargo_tanks": cargo_tanks, "grades": grades, "current": current}
    return client.chat_model(prompts.STOWAGE_INSTRUCTIONS_SYSTEM, _with_memory(compact(context), memory_lines),
                             InstructionOut, model=model, max_tokens=config.MAX_TOKENS_JSON)


def explain(system: str, digest: dict, memory_lines: list[str] | None = None, question: str | None = None) -> str:
    user = compact(for_model(digest)) +(f"\nQuestion: {question.strip()}" if question else "")
    return client.chat_text(system, _with_memory(user, memory_lines), max_tokens=config.MAX_TOKENS_EXPLAIN)
