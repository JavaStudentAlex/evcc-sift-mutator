# Physical Hardware Testbed Specifications & Interfacing

This document records the exact physical testbed hardware, electrical specifications, interface wiring, and diagnostic tools used at the **YFN x EU Energy Hackathon (Munich, 2026)** for the **EnBW Fast-Charging Robustness & Conformance Challenge**.

---

## 1. Testbed Overview & Target Apparatus

The testbench comprises two primary industrial units:
1. **Target EVSE Unit:** An official **EnBW HyperNetz 50 kW DC Fast Charger** (Alpitronic SECC architecture).
2. **Measurement & Analysis System:** A **Comemso EV Charging Analyzer (Multi Mobile System)** ruggedized flight-case unit.

```
+-----------------------------------------------------------------------------------+
|                            PHYSICAL TESTBED TOPOLOGY                              |
+-----------------------------------------------------------------------------------+

     [ Laptop Workstation ] (evcc-sift-mutator)
           │            │
    Ethernet (TCP/IP)   │ IXXAT USB-to-CAN
           │            │
           ▼            ▼
   +─────────────────────────────+
   |   Comemso Charging Analyzer | <─── Monitors CP/PP, V2GTP, & HomePlug PLC
   |      (Flight Case Unit)     |      (Measures attenuation, timings, & waveforms)
   +─────────────────────────────+
                 │
                 │ CCS Combo-2 Charging Cable (CP, PE, DC+, DC-)
                 ▼
   +─────────────────────────────+
   |  EnBW HyperNetz 50 kW SECC  | <─── Alpitronic Supply Equipment Controller
   |  (Diagnostic Breakout Mode) |      (Isolated DC bus, auxiliary powered)
   +─────────────────────────────+
```

---

## 2. Target Device: EnBW HyperNetz 50 kW Charger

* **Brand / Network:** `-EnBW HyperNetz` (`Ökostrom` certified).
* **Power Class:** `MAX. 50 kW` DC Fast Charging.
* **Connector Type:** CCS Combo 2 (IEC 62196-3).
* **Communication Controller (SECC):** Alpitronic Hypercharger industrial controller running DIN SPEC 70121 / ISO 15118-2 protocol firmware.
* **User Interface & Payment:** Embedded color LCD status screen and high-frequency RFID/NFC contactless terminal block.
* **Safety Certification:** CE marked, WEEE compliant.

### Electrical & Physical Interface Characteristics:
* **Nominal Operating DC Voltage Range:** $200\text{ V} \text{ to } 920\text{ V DC}$.
* **Maximum Current Capability:** Up to $125\text{ A}$ continuous ($50\text{ kW} @ 400\text{ V}$) or $150\text{ A}$ boost.
* **Low-Voltage Service Breakout (Left Panel):**
  * **Orange Leads:** Isolated high-voltage measurement test lines.
  * **Low-Voltage Ribbon & Harness:** Control Pilot (CP), Protective Earth (PE), Proximity Pilot (PP).
  * **CAN Bus Pins:** Direct differential interface (CAN_H, CAN_L) from internal charging controller.
  * **External Diagnostic Box:** Bench-top auxiliary interface board routing signal leads without requiring energized 400V grid AC feed.

---

## 3. Analyzer Apparatus: Comemso Multi Mobile System

* **Manufacturer:** Comemso GmbH (Automotive Testing Solutions).
* **Enclosure:** Ruggedized black Peli-style flight case with integrated high-voltage warning interlock placard.
* **Inlet Interface:** Integrated vehicle-side CCS Type 2 Combo-2 inlet accepting standard charging plugs.
* **Internal Capabilities:**
  * Real-time Control Pilot (PWM) evaluation per IEC 61851-1.
  * Powerline Communication (PLC) transceiver: HomePlug Green PHY (IEEE 1901) sniffing and MME frame decoding.
  * CAN bus monitor and frame logger (500 kbps / 250 kbps).
  * High-precision current shunts and isolation resistance measurement.

### Monitoring Software Running on Workstation:
1. **Comemso Charging Analysis Suite (GUI):**
   * Visualizes real-time state machine sequence diagrams:
     $$\text{State A} \longrightarrow \text{State B} \longrightarrow \text{SLAC} \longrightarrow \text{SupportedAppProtocol} \longrightarrow \text{SessionSetup} \longrightarrow \text{CableCheck} \longrightarrow \text{PreCharge} \longrightarrow \text{CurrentDemand}$$
   * Highlights timing window deviations (e.g. timeout on `SupportedAppProtocolRes` $> 2000\text{ ms}$).
2. **Terminal / Diagnostic Log Console:**
   * Live timestamped serial / SSH console output monitoring SECC internal firmware state transitions, contactor relay assertions, and error events.

---

## 4. Connecting the `evcc-sift-mutator` Harness

To execute search-based robustness verification against this testbed:

### Step 1: Physical Cabling
1. Connect your laptop to the local testbed switch using the **white RJ-45 Ethernet cable** (bridges into the HomePlug PLC network).
2. Connect the **IXXAT USB-to-CAN compact adapter** to an available USB 3.0 port on your laptop, and attach the D-Sub 9 connector to the CAN breakout on the table.

### Step 2: Bring up the Network & CAN Interfaces
```bash
# Verify Ethernet link-local IPv6 address (used by ISO 15118 SDP)
ip -6 addr show eth0

# Bring up SocketCAN interface at 500 kbps (standard for EVSE internal buses)
sudo ip link set can0 up type can bitrate 500000
sudo ifconfig can0 up

# Verify incoming telemetry frames
candump can0
```

### Step 3: Run the Adaptive Testing Harness
```bash
# Execute 10 automated testing cycles with adaptive SIFT mutation & self-healing
uv run python run_fuzzer.py --mode hardware --channel can0 --cycles 10 --report reports/hardware_test_findings.md
```

### Step 4: Observability During Test Execution
* **On the Charger:** Observe whether the 50 kW front LCD panel maintains state or triggers an emergency isolation fault code.
* **On the Comemso Monitor:** Watch the state machine timeline bar turn amber/red when an intentional boundary or out-of-order vector is evaluated.
* **In Terminal Logs:** Review the automatically generated CVE/CWE report in `reports/hardware_test_findings.md`.
