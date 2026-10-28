"""Typed contracts shared by every BuildLoop layer. Mirrored to TypeScript via scripts/gen_contracts.py."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


class Mode(str, Enum):
    REAL = "real"
    DEMO = "demo"


class Provenance(str, Enum):
    """Where a fact came from. Never upgrade silently."""

    DETECTED = "detected"  # model/vision prediction
    USER_CONFIRMED = "user_confirmed"
    PLANNED = "planned"
    ASSEMBLED = "assembled"  # user says it is built
    VALIDATED = "validated"  # passed deterministic rules
    PHYSICALLY_TESTED = "physically_tested"  # hardware evidence
    UNCERTAIN = "uncertain"
    FAILED = "failed"


class ComponentType(str, Enum):
    ESP32 = "esp32"
    ARDUINO = "arduino"
    BREADBOARD = "breadboard"
    LED = "led"
    RESISTOR = "resistor"
    PUSHBUTTON = "pushbutton"
    PHOTORESISTOR = "photoresistor"
    JUMPER_WIRE = "jumper_wire"
    UNKNOWN = "unknown"


class BBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    img_w: int
    img_h: int


class PinDefinition(BaseModel):
    name: str
    function: str  # "anode", "cathode", "gpio", "adc", "gnd", "3v3", "terminal"


class ComponentInstance(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cmp"))
    type: ComponentType
    name: str
    bbox: BBox | None = None
    mask_path: str | None = None  # SVG path of the SAM mask, local to mask_bounds origin
    mask_bounds: list[float] | None = None  # [left, top, right, bottom] in source-image pixels
    orientation_deg: float | None = None
    pins: list[PinDefinition] = []
    properties: dict[str, float | str | None] = {}  # None = unknown (e.g. resistance_ohm)
    confidence: float | None = None
    status: Provenance = Provenance.DETECTED
    available: bool = True
    last_observed: datetime = Field(default_factory=now)


class Connection(BaseModel):
    id: str = Field(default_factory=lambda: new_id("con"))
    src: str  # "component_id.pin"
    dst: str
    kind: Literal["signal", "power", "ground"] = "signal"
    expected: str = ""
    evidence: str = "design"
    status: Provenance = Provenance.PLANNED


class VStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class ValidationResult(BaseModel):
    rule: str
    status: VStatus
    message: str
    fix: str = ""
    estimated: bool = False  # True when value is computed, not measured


class FirmwareBehavior(BaseModel):
    sensor_source: Literal["photoresistor_adc", "phone_camera_brightness"]
    sensor_pin: int | None = None
    led_pin: int
    threshold: float  # ADC counts (0-4095) or phone brightness 0-1
    dark_means: Literal["adc_high", "adc_low", "brightness_low"]


class DesignCandidate(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dsg"))
    title: str
    goal: str
    board: str
    requires: list[ComponentType]
    components: list[ComponentInstance] = []
    connections: list[Connection] = []
    behavior: FirmwareBehavior
    tradeoffs: list[str] = []
    substitute_notes: list[str] = []
    validation: list[ValidationResult] = []
    source: str = "deterministic"  # "muse-spark" | "deterministic" | "demo-fixture"
    created: datetime = Field(default_factory=now)

    @property
    def overall(self) -> VStatus:
        for s in (VStatus.BLOCKED, VStatus.UNKNOWN, VStatus.WARNING, VStatus.PASS):
            if any(v.status == s for v in self.validation):
                return s
        return VStatus.UNKNOWN


class AssemblyStep(BaseModel):
    index: int
    title: str
    detail: str
    connection_ids: list[str] = []
    needs_confirmation: bool = True
    confirmed: bool = False


class Stage(str, Enum):
    OBSERVE = "OBSERVE"
    INTERPRET = "INTERPRET"
    CONFIRM = "CONFIRM"
    PLAN = "PLAN"
    VALIDATE = "VALIDATE"
    GUIDE = "GUIDE"
    TEST = "TEST"
    COMPARE = "COMPARE"
    REDESIGN = "REDESIGN"
    COMPLETE = "COMPLETE"


class DigitalTwinSnapshot(BaseModel):
    revision: int
    components: list[ComponentInstance]
    connections: list[Connection] = []
    taken: datetime = Field(default_factory=now)


class InventoryDiff(BaseModel):
    added: list[ComponentInstance] = []
    removed: list[ComponentInstance] = []
    changed: list[str] = []  # component ids whose properties changed


class VerificationLevel(str, Enum):
    NONE = "none"
    COMMAND_ACKED = "command_acknowledged"
    GPIO_REPORTED = "gpio_state_reported"
    SENSOR_OBSERVED = "sensor_response_observed"
    OPTICAL_OBSERVED = "optical_output_observed"  # independent evidence only
    PHYSICAL_VERIFIED = "physical_behavior_verified"


class Evidence(BaseModel):
    kind: str
    detail: str
    level: VerificationLevel
    simulated: bool = False
    at: datetime = Field(default_factory=now)


class VerificationResult(BaseModel):
    design_id: str
    passed: bool | None  # None = inconclusive
    level: VerificationLevel
    evidence: list[Evidence]
    contradictions: list[str] = []
    summary: str = ""


class RevisionEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rev"))
    project_id: str
    kind: str  # inventory_change | design_created | design_invalidated | validation | hardware_test | stage | decision
    summary: str
    data: dict = {}
    at: datetime = Field(default_factory=now)


class Project(BaseModel):
    id: str = Field(default_factory=lambda: new_id("prj"))
    name: str
    goal: str = ""
    mode: Mode = Mode.DEMO
    board: str = "esp32-devkitc-v4"
    stage: Stage = Stage.OBSERVE
    created: datetime = Field(default_factory=now)


class TelemetryRecord(BaseModel):
    at: datetime = Field(default_factory=now)
    source: Literal["hardware", "simulated", "phone"]
    adc: int | None = None
    led_state: bool | None = None
    raw: dict = {}


class ApiError(BaseModel):
    code: str
    message: str
    hint: str = ""
