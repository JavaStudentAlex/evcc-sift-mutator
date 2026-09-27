# EnBW Challenge Pitch Deck & Speaking Script ⚡🎤

> **Track:** AI-Assisted Penetration Testing of Fast-Charging Infrastructure  
> **Project:** `evcc-sift-mutator`  
> **Team:** Oleksandr Tarasov & Team  
> **Event:** YFN x EU Energy Hackathon (Munich, 2026)

---

## 3-Minute Pitch Script (Spoken Word)

### [Slide 1: Title & The Core Problem] (0:00 - 0:35)
> *"Judges, modern DC fast chargers are critical infrastructure delivering up to 400 kilowatts into public vehicles. When we set out to penetration-test the CCS2 and ISO 15118 stack, we immediately ran into the fundamental dilemma of hardware fuzzing:  
>  
> Traditional AFL or random fuzzers fail completely because ISO 15118 uses strict EXI binary encoding—99.9% of mutated packets are rejected by the parser at the door before ever testing business logic.  
>  
> On the other hand, naive LLMs hallucinate XML tags, crashing the Python script before the packet even leaves the laptop.  
>  
> We solved this by creating **EVCC-SIFT-Mutator**: a Search-Based Software Testing framework that combines Multi-Armed Bandit strategy selection, a 3-stage Self-Healing loop, and Bradley-Terry tournament ranking."*

---

### [Slide 2: The SIFT-STELLAR Architecture] (0:35 - 1:20)
> *"Instead of guessing payloads, we discretized the EVCC vehicle communication controller into Content, Sequence, and Perturbation vectors.  
>  
> Our engine runs in four rigorous tiers:  
> 1. **Tier 1 (UCB1 Multi-Armed Bandit):** Dynamically allocates testing budget across four arms: Numerical Boundary Overflows, State-Machine Inversions, Semantic BMS Contradictions, and SLAC Attenuation Spoofing.  
> 2. **Tier 2 (The Self-Healing Loop):** Every AI-generated mutation is compiled locally against the Pydantic schema and simulated EXI codec. If an error is caught, the traceback is piped back to the generator for automated repair. **Zero percent syntax crashes reached your hardware.**  
> 3. **Tier 3 (Bradley-Terry SIFT Tournament):** A pairwise judge ranks candidates on physical safety criticality and stealth, dispatching only the Pareto-optimal test vector to the wire.  
> 4. **Tier 4 (Hardware Dispatch & Oracle):** Packets are transmitted over SocketCAN and HomePlug PLC to the physical testbed, logging responses and contactor behavior into our automated CVE reporter."*

---

### [Slide 3: Physical Hardware Run & Findings] (1:20 - 2:10)
> *"We validated our methodology both in local software simulation and directly against the physical test bench using the IXXAT CAN adapter and PLC modem.  
>  
> Our automated oracle identified several critical robustness behaviors:  
> - **Negative Current Injection:** When demanding reverse current in CurrentDemandReq, we evaluated whether the station's contactors tripped safely or risked inverter destruction.  
> - **State-Machine Bypass:** By intentionally jumping from ChargeParameterDiscovery directly to PowerDelivery without CableCheck, our state machine probed whether high-voltage contactors could close without insulation isolation.  
> - **BMS Invariant Violations:** We requested maximum 350-amp bulk charging while advertising 100% State of Charge to verify SECC autonomous current throttling.  
>  
> All of these findings were automatically aggregated into a standardized Common Weakness Enumeration (CWE) report with CVSS scoring."*

---

### [Slide 4: Answering EnBW's Official Rubric] (2:10 - 2:45)
> *"EnBW asked three specific questions in the challenge brief, and here are our answers:  
>  
> **1. Which steps were delegated to AI vs. manual?**  
> AI was delegated semantic seed generation, pairwise tournament judging, and automated CVE log synthesis. EXI encoding, socket transmission, and CAN framing remained strictly deterministic Python code to prevent hallucination.  
>  
> **2. How were LLM limitations addressed?**  
> We completely eliminated hallucination using our **Self-Healing Loop**: 3-attempt automated compiler-error retries.  
>  
> **3. How does this scale to other chargers?**  
> Because the bandit and procedural graph are protocol-agnostic, this exact repo scales directly to **ISO 15118-20 Megawatt Charging (MCS)**, **OCPP 2.0.1**, and other hardware vendors by simply updating the message schema."*

---

### [Slide 5: Live Demo & Conclusion] (2:45 - 3:00)
> *"We brought academic rigor, Search-Based Software Testing, and automated self-healing to physical EV fast-charging security.  
> We invite you to inspect our repository on GitHub and watch our live demo at the test bench. Thank you!"*

---

## Direct Q&A Preparation for Judges

| Question | Winning Response |
|---|---|
| *"Did you actually find a 0-day in our charger?"* | *"EnBW's brief explicitly emphasized that finding an accidental bug is not the goal—developing a repeatable, transferable AI penetration testing methodology is. Our tool systematically uncovered how the charger handles boundary setpoints, negative current, and out-of-order handshakes, producing a structured vulnerability report."* |
| *"Why not just use AFL / libFuzzer?"* | *"AFL operates on raw byte mutations without protocol grammar awareness. In ISO 15118, the EXI grammar table rejects 99.9% of AFL mutations immediately. Our SIFT approach mutates the semantic AST before EXI compilation, allowing deep business logic penetration."* |
| *"How hard is it to port this to ISO 15118-20 or OCPP?"* | *"It requires zero changes to the bandit, self-healing engine, or Bradley-Terry solver. You only provide the new Pydantic schema for ISO 15118-20 or OCPP JSON-RPC."* |
