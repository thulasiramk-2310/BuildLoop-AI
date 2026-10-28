"""Narrow, read-mostly tool interface exposed to Muse Spark.

The model can only call these functions. None executes hardware actions; propose_circuit builds a candidate
from trusted generators and runs the deterministic validator. Arbitrary model text never becomes a connection.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .boards import get_board
from .designs import build_adaptive_light, build_phone_sensor_light, generate_assembly_steps
from .models import ComponentInstance, DesignCandidate

CATALOG: dict[str, dict[str, Any]] = {
    "esp32": {"logic_v": 3.3, "five_v_tolerant": False, "note": "ADC1 pins 32-39 only with WiFi; GPIO34-39 input only"},
    "led": {"forward_v_typ": {"red": 2.0, "green": 2.2, "blue": 3.0, "white": 3.0}, "note": "needs series resistor; long leg anode"},
    "resistor": {"note": "value via colour bands; confirm if unknown"},
    "photoresistor": {"resistance_bright_ohm": "1k-10k", "resistance_dark_ohm": ">=100k", "note": "use with fixed divider resistor"},
    "pushbutton": {"note": "debounce in firmware; use INPUT_PULLUP"},
    "breadboard": {"note": "rails may be split mid-board"},
}


class ToolContext:
    def __init__(self, inventory: list[ComponentInstance], board_id: str, goal: str, telemetry_summary: str = "", last_verification: dict | None = None):
        self.inventory, self.board_id, self.goal = inventory, board_id, goal
        self.telemetry_summary = telemetry_summary
        self.last_verification = last_verification
        self.candidates: dict[str, DesignCandidate] = {}
        self.trace: list[dict] = []  # tool name, args, result summary (no hidden reasoning)


def _get_available_components(ctx: ToolContext, **_: Any) -> Any:
    return [
        {"id": c.id, "type": c.type.value, "name": c.name, "status": c.status.value, "properties": c.properties}
        for c in ctx.inventory
        if c.available
    ]


def _get_board_capabilities(ctx: ToolContext, board_id: str | None = None, **_: Any) -> Any:
    b = get_board(board_id or ctx.board_id)
    return {
        "id": b.id, "logic_v": b.logic_v, "five_v_tolerant": b.five_v_tolerant,
        "output_pins": sorted(b.output_pins), "adc_pins": sorted(b.adc_pins), "input_only": sorted(b.input_only),
        "reserved": sorted(b.reserved), "strapping": sorted(b.strapping), "gpio_max_ma": b.gpio_max_ma,
        "demo_led_pin": b.demo_led_pin, "demo_adc_pin": b.demo_adc_pin, "notes": list(b.notes),
    }


def _retrieve_component_specification(ctx: ToolContext, component_type: str = "", **_: Any) -> Any:
    return CATALOG.get(component_type, {"error": f"no spec for '{component_type}'", "known": sorted(CATALOG)})


def _validate_connection(ctx: ToolContext, pin: int = -1, role: str = "output", **_: Any) -> Any:
    b = get_board(ctx.board_id)
    ok = {"output": pin in b.output_pins, "adc": pin in b.adc_pins, "input": pin in b.adc_pins | b.output_pins}.get(role, False)
    return {"pin": pin, "role": role, "ok": ok, "reserved": pin in b.reserved, "strapping": pin in b.strapping}


def _validate_power_requirements(ctx: ToolContext, load_ma: float = 0.0, **_: Any) -> Any:
    b = get_board(ctx.board_id)
    return {"load_ma": load_ma, "pin_budget_ma": b.gpio_max_ma, "ok": load_ma <= b.gpio_max_ma, "logic_v": b.logic_v}


def _propose_circuit(ctx: ToolContext, sensor_source: str = "photoresistor_adc", **_: Any) -> Any:
    board = get_board(ctx.board_id)
    if sensor_source == "photoresistor_adc":
        d = build_adaptive_light(ctx.inventory, board, ctx.goal)
    elif sensor_source == "phone_camera_brightness":
        d = build_phone_sensor_light(ctx.inventory, board, ctx.goal)
    else:
        return {"error": f"unsupported sensor_source '{sensor_source}'", "supported": ["photoresistor_adc", "phone_camera_brightness"]}
    d.source = "muse-spark"
    ctx.candidates[d.id] = d
    return {"design_id": d.id, "overall": d.overall.value, "validation": [v.model_dump(mode="json") for v in d.validation]}


def _compare_design_alternatives(ctx: ToolContext, **_: Any) -> Any:
    return [{"design_id": i, "title": d.title, "overall": d.overall.value, "tradeoffs": d.tradeoffs, "substitute_notes": d.substitute_notes} for i, d in ctx.candidates.items()]


def _generate_assembly_steps(ctx: ToolContext, design_id: str = "", **_: Any) -> Any:
    d = ctx.candidates.get(design_id)
    return [s.model_dump() for s in generate_assembly_steps(d)] if d else {"error": "unknown design_id"}


def _inspect_hardware_telemetry(ctx: ToolContext, **_: Any) -> Any:
    return {"summary": ctx.telemetry_summary or "no telemetry available"}


def _evaluate_verification_results(ctx: ToolContext, **_: Any) -> Any:
    return ctx.last_verification or {"summary": "no verification has run"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


TOOLS: dict[str, tuple[Callable[..., Any], str, dict]] = {
    "get_available_components": (_get_available_components, "List confirmed/available components.", _obj({})),
    "get_board_capabilities": (_get_board_capabilities, "Pin capabilities for the target board.", _obj({"board_id": {"type": "string"}})),
    "retrieve_component_specification": (_retrieve_component_specification, "Reference data for a component type.", _obj({"component_type": {"type": "string"}}, ["component_type"])),
    "validate_connection": (_validate_connection, "Check a GPIO number for a role (output|adc|input).", _obj({"pin": {"type": "integer"}, "role": {"type": "string"}}, ["pin", "role"])),
    "validate_power_requirements": (_validate_power_requirements, "Check a load current against the pin budget.", _obj({"load_ma": {"type": "number"}}, ["load_ma"])),
    "propose_circuit": (_propose_circuit, "Build and validate a candidate. sensor_source: photoresistor_adc | phone_camera_brightness.", _obj({"sensor_source": {"type": "string", "enum": ["photoresistor_adc", "phone_camera_brightness"]}}, ["sensor_source"])),
    "compare_design_alternatives": (_compare_design_alternatives, "Compare proposed candidates.", _obj({})),
    "generate_assembly_steps": (_generate_assembly_steps, "Assembly steps for a candidate.", _obj({"design_id": {"type": "string"}}, ["design_id"])),
    "inspect_hardware_telemetry": (_inspect_hardware_telemetry, "Read-only telemetry summary.", _obj({})),
    "evaluate_verification_results": (_evaluate_verification_results, "Read-only last verification result.", _obj({})),
}


def tool_specs() -> list[dict]:
    return [{"type": "function", "name": n, "description": d, "parameters": p} for n, (_f, d, p) in TOOLS.items()]


def _brief(r: Any) -> Any:
    s = str(r)
    return r if len(s) < 400 else s[:400] + "..."


def call_tool(ctx: ToolContext, name: str, args: dict) -> Any:
    """Dispatch. Unknown tool names (e.g. an attempted hardware action) are rejected and traced."""
    if name not in TOOLS:
        result: Any = {"error": f"tool '{name}' is not available", "rejected": True}
    else:
        try:
            result = TOOLS[name][0](ctx, **(args or {}))
        except (KeyError, TypeError, ValueError) as e:
            result = {"error": str(e)}
    ctx.trace.append({"tool": name, "args": args, "result": _brief(result)})
    return result
