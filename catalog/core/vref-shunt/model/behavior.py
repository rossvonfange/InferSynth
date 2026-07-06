"""Behavioral model for vref-shunt (sim-gate v0 tier, docs/SIM.md).

Exports ``make_behavior(params) -> Block`` where ``params`` carries
``r_bias_ohms`` (the single idiom param; cell.yaml binds ``R1: "r_bias_ohms"``).
The block's ports match cell.yaml ``ports`` exactly: VIN, VREF, GND.

TL431-class shunt reference, referenced to GND::

    required_vin = VREF_NOMINAL + MIN_CATHODE_A * r_bias_ohms
    VREF = VREF_NOMINAL                     if (VIN - GND) >= required_vin
         = min(VREF_NOMINAL, VIN - GND)     otherwise

``VREF_NOMINAL = 2.495 V`` is the TL431's nominal reference voltage.
``MIN_CATHODE_A = 1 mA`` is the datasheet minimum cathode current the device
needs to regulate; below that, R1 can no longer sustain regulation and the
device falls out of shunt mode.

**Simplification, documented honestly (this is a v0 ideal-tier model, not a
SPICE-accurate TL431):** when there isn't enough headroom to regulate, the real
device's dropout behavior (base-emitter/internal-reference dynamics) is not
modeled at all. This tier just clamps VREF to ``VIN`` (assuming a negligible
drop through R1 at the low currents involved) -- an honest approximation, not a
claim of physical accuracy in the dropout region. Callers relying on precise
low-VIN behavior should treat this cell's regulated region (VIN above
``required_vin``) as the only characterized operating point.
"""

from __future__ import annotations

from collections.abc import Mapping

#: TL431 nominal reference voltage (volts). Fixed by the archetype device, not
#: an idiom param (cell.yaml has no configurable reference voltage -- "fixed
#: 2.5V configuration" per the cell's manifest note).
VREF_NOMINAL = 2.495

#: TL431 datasheet minimum cathode current to sustain regulation (amps).
MIN_CATHODE_A = 1.0e-3


class ShuntReference:
    """TL431-class shunt reference: regulates VREF while cathode current holds."""

    inputs = ("VIN", "GND")
    outputs = ("VREF",)

    def __init__(self, r_bias_ohms: float, name: str = "vref_shunt"):
        self.name = name
        self.r_bias_ohms = float(r_bias_ohms)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        gnd = inputs["GND"]
        vin_rel = inputs["VIN"] - gnd
        required_vin = VREF_NOMINAL + MIN_CATHODE_A * self.r_bias_ohms
        if vin_rel >= required_vin:
            vref_rel = VREF_NOMINAL
        else:
            # Simplification documented above: below the regulation threshold,
            # VREF follows VIN (negligible-drop assumption), capped at nominal.
            vref_rel = min(VREF_NOMINAL, vin_rel)
        return {"VREF": gnd + vref_rel}


def make_behavior(params: Mapping[str, object]):
    """Build the DUT block from resolved idiom params (needs ``r_bias_ohms``)."""
    return ShuntReference(r_bias_ohms=float(params["r_bias_ohms"]))  # type: ignore[arg-type]
