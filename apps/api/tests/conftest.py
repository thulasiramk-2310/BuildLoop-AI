import pytest

from buildloop.config import Settings
from buildloop.designs import default_pins
from buildloop.main import create_app
from buildloop.models import ComponentInstance, ComponentType, Provenance


def comp(t: ComponentType, name: str, **props) -> ComponentInstance:
    return ComponentInstance(id=f"{t.value}_{name}", type=t, name=name, pins=default_pins(t), properties=props, status=Provenance.USER_CONFIRMED)


@pytest.fixture
def inventory():
    return [
        comp(ComponentType.ESP32, "esp"),
        comp(ComponentType.LED, "led", forward_v=2.0),
        comp(ComponentType.RESISTOR, "r220", resistance_ohm=220),
        comp(ComponentType.RESISTOR, "r10k", resistance_ohm=10000),
        comp(ComponentType.PHOTORESISTOR, "ldr"),
        comp(ComponentType.BREADBOARD, "bb"),
    ]


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 't.db'}", MODEL_API_KEY=""))
