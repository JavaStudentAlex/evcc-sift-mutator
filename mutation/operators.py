"""Domain-Specific Protocol Mutation Operators for EV Charging Security Testing.

Implements concrete mutation logic for:
  - Numerical Boundary & Integer Overflow Fuzzing
  - State Machine Sequence Inversion & Step Skipping
  - Semantic Logic Spoofing & Power Oversubscription
  - SLAC Attenuation Profile Perturbation
"""
from __future__ import annotations

import copy
import random
from typing import Any, Dict, List, Optional, Tuple

from protocols.iso15118_messages import (
    ChargeParameterDiscoveryReq,
    ChargeProgress,
    CurrentDemandReq,
    PowerDeliveryReq,
    PreChargeReq,
    V2GMessage,
)
from protocols.slac import SlacFrame, SlacMmeType
from mutation.bandit import MutationArm


class MutationCandidate:
    """Encapsulates a generated mutation, its metadata, and rationale."""

    def __init__(self, arm: str, description: str, mutated_payload: Any, rationale: str = ""):
        self.arm = arm
        self.description = description
        self.mutated_payload = mutated_payload
        self.rationale = rationale

    def __repr__(self) -> str:
        return f"<MutationCandidate arm={self.arm} desc='{self.description}'>"


# ==========================================
# 1. Numerical Boundary Mutations
# ==========================================

def mutate_numerical_boundary(msg: V2GMessage) -> MutationCandidate:
    """Injects extreme boundary, negative, or overflow numerical values."""
    mutated = copy.deepcopy(msg)
    desc = "No boundary mutation applied"
    rationale = ""

    if isinstance(mutated, CurrentDemandReq):
        choice = random.choice(["negative_current", "overflow_current", "zero_voltage", "extreme_voltage"])
        if choice == "negative_current":
            mutated.ev_target_current = -50.0
            desc = "CurrentDemandReq: Negative current injection (-50A)"
            rationale = "Tests whether SECC contactors open or crash when car requests negative current (reverse power injection)."
        elif choice == "overflow_current":
            mutated.ev_target_current = 65535.0
            desc = "CurrentDemandReq: 16-bit integer overflow current (65535A)"
            rationale = "Probes integer truncation in SECC DSP / microcontroller current loop."
        elif choice == "zero_voltage":
            mutated.ev_target_voltage = 0.0
            desc = "CurrentDemandReq: Zero target voltage while requesting power"
            rationale = "Tests SECC divide-by-zero or power calculation instability."
        elif choice == "extreme_voltage":
            mutated.ev_target_voltage = 1500.0
            desc = "CurrentDemandReq: Extreme voltage request (1500V, exceeds 1000V limit)"
            rationale = "Tests SECC high-voltage isolation shutdown and hardware limit assertion."

    elif isinstance(mutated, PreChargeReq):
        choice = random.choice(["negative_voltage", "overvoltage"])
        if choice == "negative_voltage":
            mutated.ev_target_voltage = -10.0
            desc = "PreChargeReq: Negative target voltage (-10V)"
            rationale = "Tests DC precharge controller polarity checking."
        else:
            mutated.ev_target_voltage = 1200.0
            desc = "PreChargeReq: Target voltage above DC bus insulation limit (1200V)"
            rationale = "Tests pre-charge contactor interlocking."

    elif isinstance(mutated, ChargeParameterDiscoveryReq):
        mutated.dc_ev_charge_parameter.dc_max_current_limit = 999.0
        mutated.dc_ev_charge_parameter.dc_max_voltage_limit = 1200.0
        desc = "ChargeParameterDiscoveryReq: Out-of-spec max limits (999A, 1200V)"
        rationale = "Tests SECC parameter compatibility negotiation against rated station caps."

    return MutationCandidate(arm=MutationArm.ARM_NUMERICAL_BOUNDARY, description=desc, mutated_payload=mutated, rationale=rationale)


# ==========================================
# 2. Sequence Inversion Mutations
# ==========================================

def mutate_sequence_inversion(current_state: str, next_state: str) -> Tuple[str, str]:
    """Alters the state machine transition order to test unhandled state leaps."""
    # Examples of hazardous transitions:
    # 1. Skip Cable Check directly to PreCharge
    # 2. Skip PreCharge directly to Power Delivery Start
    # 3. Premature Current Demand while contactors are open
    if current_state == "CHARGE_PARAMETERS":
        return "PRE_CHARGE", "Skipped CABLE_CHECK state directly to PRE_CHARGE"
    elif current_state == "CABLE_CHECK":
        return "POWER_DELIVERY_START", "Skipped PRE_CHARGE directly to POWER_DELIVERY_START"
    elif current_state == "SUPPORTED_APP_PROTOCOL":
        return "POWER_DELIVERY_START", "Skipped entire negotiation to immediate POWER_DELIVERY_START"

    return next_state, "Normal transition preserved"


# ==========================================
# 3. Semantic Spoofing Mutations
# ==========================================

def mutate_semantic_spoof(msg: V2GMessage) -> MutationCandidate:
    """Injects logical contradictions (e.g. 100% SoC requesting full bulk current)."""
    mutated = copy.deepcopy(msg)
    desc = "No semantic mutation applied"
    rationale = ""

    if isinstance(mutated, CurrentDemandReq):
        choice = random.choice(["full_soc_max_current", "complete_flag_contradiction"])
        if choice == "full_soc_max_current":
            mutated.state_of_charge = 100.0
            mutated.bulk_soc = 100.0
            mutated.ev_target_current = 350.0  # Full power at 100%
            desc = "CurrentDemandReq: 100% SoC with Maximum 350A Current Demand"
            rationale = "Violates BMS safety model; tests whether SECC overrides EVCC or risks thermal runaway."
        else:
            mutated.charging_complete = True
            mutated.ev_target_current = 200.0
            desc = "CurrentDemandReq: charging_complete=True while actively requesting 200A"
            rationale = "Contradictory boolean state; tests charger logic priority."

    return MutationCandidate(arm=MutationArm.ARM_SEMANTIC_SPOOF, description=desc, mutated_payload=mutated, rationale=rationale)


# ==========================================
# 4. SLAC Attenuation Mutations
# ==========================================

def mutate_slac_frame(frame: SlacFrame) -> MutationCandidate:
    """Mutates HomePlug Green PHY SLAC frames."""
    mutated = copy.deepcopy(frame)
    choice = random.choice(["corrupt_run_id", "malformed_sound_count", "spoofed_attenuation"])

    if choice == "corrupt_run_id":
        mutated.run_id = b"\x00" * 8
        desc = "SLAC: All-zero RunID injection"
        rationale = "Tests SECC pairing table collision or session desynchronization."
    elif choice == "malformed_sound_count":
        mutated.payload = b"\xFF\xFF" + b"\x00" * 10
        desc = "SLAC: 255 sounding packets declared in single indicator"
        rationale = "Tests SECC buffer bounds during attenuation averaging calculation."
    else:
        mutated.payload = b"\x00\x00" + b"\x7F" * 58
        desc = "SLAC: Extreme 127dB cable loss attenuation profile"
        rationale = "Tests SECC minimum signal-to-noise ratio thresholding."

    return MutationCandidate(arm=MutationArm.ARM_SLAC_ATTENUATION, description=desc, mutated_payload=mutated, rationale=rationale)
