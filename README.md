# EVCC-SIFT-Mutator ⚡🛡️

> **Search-Based Protocol Mutation, Self-Healing Fuzzing Harness, and Procedural Decision Graph for ISO 15118 / DIN SPEC 70121 EV Fast Charging Security Auditing.**

Developed for the **YFN x EU Energy Hackathon (Munich, 2026)** — EnBW Track: *AI-Assisted Penetration Testing of Fast-Charging Infrastructure*.

---

## 🌟 Executive Overview

Modern DC fast chargers (CCS2 / High Power Chargers) rely on complex digital handshakes between the electric vehicle (EVCC) and the charging station (SECC) over **HomePlug Green PHY Powerline Communication (PLC)**, **ISO 15118-2 / ISO 15118-20 / DIN SPEC 70121**, and internal **CAN (SAE J1939)** buses.

Traditional fuzzing (e.g. random bit flipping or AFL) fails against ISO 15118 because strict **Efficient XML Interchange (EXI)** schemas and TLS handshakes reject 99.9% of mutated packets at the deserialization layer before reaching application logic. Conversely, naive LLMs hallucinate invalid schemas, breaking execution.

**EVCC-SIFT-Mutator** bridges this gap by combining:
1. **The STELLAR Framework (Search-Based Software Testing / SBST):** Discretizing protocol messages into Content, Sequence, and Perturbation vectors.
2. **SIFT Architecture (Multi-Armed Bandit + Bradley-Terry Tournament):** UCB1 selection across specialized mutation arms, paired with a Bradley-Terry pairwise preference tournament.
3. **Self-Healing Validation Loop (`SIFT_MUTATION_RETRIES = 3`):** Intercepts syntax/codec errors locally, pipes tracebacks back to the AI/mutator, and guarantees **100% syntactically valid wire frames** reaching the target.
4. **Self-Optimizing Procedural Decision Graph:** An evolving operational graph that tunes exploration constants, candidate pool sizes, and reward weights based on charger distress signals.

---

## 🏗️ Architecture & Pipeline

```
                                  +---------------------------------------------+
                                  |   Procedural Policy Graph (Self-Improving) |
                                  +---------------------------------------------+
                                                         |
         +-----------------------------------------------+-----------------------------------------------+
         |                                               |                                               |
         v                                               v                                               v
[ Tier 1: UCB1 Bandit ]                    [ Tier 2: Self-Healing Engine ]                  [ Tier 3: SIFT Judge ]
Selects Mutation Strategy:                 Validates against Pydantic / EXI                 Pairwise Bradley-Terry
  * Numerical Boundary (V, I)              Catches tracebacks locally                       tournament ranking across
  * Sequence Inversion (State skips)       3-attempt automated repair                       candidate test vectors:
  * Semantic Spoofing (100% SoC)           0% LLM syntax crashes reach wire                   * Safety Criticality
  * SLAC Attenuation MME                                                                      * Novelty & Stealth
         |                                               |                                               |
         +-----------------------------------------------+-----------------------------------------------+
                                                         |
                                                         v
                                      +------------------------------------+
                                      |   Hardware / Mock Dispatch Node    |
                                      +------------------------------------+
                                            /                        \
                                           v                          v
                        [ Physical Testbed ]               [ Local SECC Mock Server ]
                        * IXXAT USB-to-CAN                 * 100% Offline Software Dev
                        * PLC Modem / Ethernet             * Simulates contactor trips,
                        * CCS2 Fast Charger SECC             isolation faults, & errors
                                           \                          /
                                            v                        v
                                      +------------------------------------+
                                      |     Oracle & CVE Report Generator  |
                                      +------------------------------------+
                                      * Anomaly & ResponseCode detector
                                      * Automated CWE / CVSS-scored audit
```

---

## 🚀 Quickstart & Usage

### 1. Installation

Using `uv` (recommended) or standard `pip`:

```bash
# Clone the repository
git clone https://github.com/JavaStudentAlex/evcc-sift-mutator.git
cd evcc-sift-mutator

# Install dependencies
uv sync --all-extras
# or: pip install -e ".[dev,hardware]"
```

### 2. Run Local Fuzzing in Mock Mode (No Hardware Required)

You can run the entire evolutionary fuzzing pipeline, self-healing validation, and report generation in pure software:

```bash
uv run python run_fuzzer.py --mode mock --cycles 5
```

Output:
```
======================================================================
  EVCC-SIFT-MUTATOR: Adaptive Protocol Security Harness
  Mode: MOCK | Cycles: 5 | Target: ISO 15118 / DIN 70121
======================================================================
--- [Iteration 1/5] Starting EVCC Charging Handshake ---
  -> Procedural Cycle Completed: Arm=ARM_NUMERICAL_BOUNDARY, Reward=4.00
    [ANOMALY TRIGGERED] SAFETY HAZARD: Requested power exceeds SECC rating! V=1500.0V (Max 1000.0V)
--- [Iteration 2/5] Starting EVCC Charging Handshake ---
  -> Procedural Cycle Completed: Arm=ARM_SEMANTIC_SPOOF, Reward=4.00
    [ANOMALY TRIGGERED] NEGATIVE POWER INJECTION: Target V=400.0, Target I=-50.0

✓ Security report successfully written to: reports/security_findings.md
```

### 3. Run Physical Hardware Mode (IXXAT USB-to-CAN / SocketCAN)

When plugged into the real charging station test bench:

```bash
# Bring up SocketCAN interface (if using Linux vcan or peak/ixxat driver)
sudo ip link set can0 up type can bitrate 500000

# Execute live fuzzer against the physical charger
uv run python run_fuzzer.py --mode hardware --channel can0 --cycles 10
```

---

## 📂 Repository Structure

| Directory / File | Description |
|---|---|
| `HARDWARE_TESTBED.md` | **Physical Testbed Guide:** Complete documentation of the EnBW HyperNetz 50 kW charger, Comemso Multi Mobile Analyzer, wiring breakouts, and hardware connection steps. |
| `protocols/` | Conformance models: ISO 15118-2/20 dataclasses, V2GTP header codec, HomePlug Green PHY SLAC, CAN J1939. |
| `simulator/` | Virtual EVCC state machine (`state_machine.py`), EnBW integration hooks (`hooks.py`), `SeccMockServer`, and IXXAT hardware driver. |
| `mutation/` | UCB1 Multi-Armed Bandit, Domain Operators, Self-Healing Validation Loop (`self_healing.py`), SIFT Pairwise Judge (`sift_judge.py`). |
| `procedural_graph/`| Executable procedural decision graph runtime (`graph_runtime.py`) and self-optimizing parameter mutator (`graph_mutator.py`). |
| `oracles/` | Anomaly detector for ResponseCodes / CAN errors, PCAP analyzer, and automated CVE/CWE Markdown report compiler. |
| `run_fuzzer.py` | Unified CLI testbed runner supporting both mock and physical hardware execution. |
| `ARCHITECTURE.md` | Formal specification of STELLAR discretization, SIFT tournament dynamics, and Bradley-Terry solver. |
| `HACKATHON_PITCH.md`| Script and slide outline addressing every question in EnBW's judging rubric. |

---

## 🏆 Hackathon Rubric Alignment (EnBW Track)

1. **Which steps were delegated to AI, and which remained manual?**  
   * *AI Role:* Generating semantic mutation seeds, ranking test cases via pairwise Bradley-Terry judging, and interpreting raw PCAP/CAN logs into CVE reports.  
   * *Deterministic Role:* EXI binary serialization, V2GTP framing, and SocketCAN transmission remain strictly deterministic Python code to prevent hallucination.
2. **Where were the limitations (LLM hallucinations) and how were they addressed?**  
   * Solved via our **Self-Healing Loop (`SIFT_MUTATION_RETRIES = 3`)**: syntax errors are caught locally and fed back to the generator with compiler tracebacks. Exactly 0% invalid syntax frames reach the target.
3. **How does this methodology scale to other charging stations?**  
   * The UCB1 bandit and procedural graph are protocol-agnostic; swapping dataclasses transfers the framework immediately to **ISO 15118-20 (MCS)** or **OCPP 2.0.1**.

---

## 📄 License
MIT License. Developed by Oleksandr Tarasov (2026).
