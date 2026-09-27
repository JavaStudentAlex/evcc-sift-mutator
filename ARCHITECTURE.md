# Technical Architecture & Theoretical Foundations

This document details the mathematical, protocol, and algorithmic foundations of **EVCC-SIFT-Mutator**, bridging academic Search-Based Software Testing (SBST) with industrial electric vehicle fast-charging security.

---

## 1. Problem Formulation & Protocol Layers

In high-power DC fast-charging (CCS2 / ISO 15118 / DIN SPEC 70121), the Vehicle Communication Controller (EVCC) and the Supply Equipment Communication Controller (SECC) interact across multiple protocol layers:

```
[ Application Layer ]     ISO 15118-2 / ISO 15118-20 / DIN SPEC 70121
                            └─ EXI (Efficient XML Interchange) Encoding
[ Session / Transport ]    V2GTP (8-byte header) over TLS 1.2/1.3 or plain TCP
[ Network Layer ]          IPv6 SLAAC / Link-Local Addressing
[ Data Link Layer ]        HomePlug Green PHY (IEEE 1901) & SLAC (ISO 15118-3)
[ Physical Layer ]         Control Pilot (PWM) + DC Power Contactors
[ Internal Bus ]           SAE J1939 / CAN 2.0B between SECC and Inverter/BMS
```

### The Fuzzing Dilemma in ISO 15118
1. **Random Mutation Failure:** EXI is a grammar-based schema-informed bitstream. Random byte flipping corrupts the grammar table, causing the parser to drop the packet at Layer 6 without exercising business logic.
2. **Pure LLM Generation Failure:** Language models frequently hallucinate XML tag names, violate numerical bounds, or break strict Pydantic types, leading to script crashes.
3. **The Solution:** A hybrid architecture combining **deterministic schema validation**, **search-based feature discretization**, and **multi-armed bandit LLM prompting**.

---

## 2. STELLAR Protocol Discretization

Adapted from the STELLAR framework (Search-Based Testing for AI & Autonomous Systems), we discretize the infinite protocol input space into a structured test vector $\mathbf{x} = \langle F_C, F_S, F_P \rangle$:

### A. Content Features ($F_C$)
Numerical and categorical fields inside active V2G messages:
* $V_{\text{target}} \in [-10.0, 1500.0]\text{ V}$ (Nominal: $400\text{--}800\text{ V}$)
* $I_{\text{target}} \in [-50.0, 65535.0]\text{ A}$ (Nominal: $0\text{--}400\text{ A}$)
* $\text{SoC} \in [0.0, 100.0]\%$
* $\text{EnergyTransferMode} \in \{\text{DC\_core}, \text{DC\_extended}, \text{DC\_combo\_core}\}$

### B. Sequence Features ($F_S$)
Permutations and leaps within `Iso15118EvccStateMachine`:
* $S_{\text{path}} = [s_0, s_1, \dots, s_k]$ where illegal transitions include:
  * $\text{CABLE\_CHECK} \xrightarrow{\text{skip}} \text{POWER\_DELIVERY\_START}$ (skipping insulation check)
  * $\text{SUPPORTED\_APP} \xrightarrow{\text{skip}} \text{CURRENT\_DEMAND}$ (unauthorized power draw)
  * $\text{SESSION\_SETUP} \xrightarrow{\text{replay}} \text{SESSION\_SETUP}$ (session desynchronization)

### C. Perturbation Features ($F_P$)
Layer 2/3 hardware and attenuation tampering:
* Attenuation profile vectors: $A \in \mathbb{R}^{58}$ across 58 OFDM carriers.
* Sounding packet count mismatch: declaring $N_{\text{sound}} = 10$ while sending 255.

---

## 3. Tier 1: Multi-Armed Bandit (UCB1 Formulation)

Rather than uniformly distributing fuzzing budget, we model mutation arm selection as a Multi-Armed Bandit problem.

Let $K = 4$ be the set of mutation arms:
1. $a_1$: `ARM_NUMERICAL_BOUNDARY`
2. $a_2$: `ARM_SEQUENCE_INVERSION`
3. $a_3$: `ARM_SEMANTIC_SPOOF`
4. $a_4$: `ARM_SLAC_ATTENUATION`

At step $t$, the bandit selects arm $a_t$ maximizing the Upper Confidence Bound:

$$\text{score}_i(t) = \hat{\mu}_i(t) + c \cdot \sqrt{\frac{\ln t}{N_i(t)}}$$

Where:
* $\hat{\mu}_i(t) = \frac{R_i(t)}{N_i(t)}$ is the empirical mean reward observed from arm $i$.
* $N_i(t)$ is the number of times arm $i$ was pulled.
* $c = \sqrt{2} \approx 1.414$ controls exploration vs exploitation.

### Reward Function Formulation:
$$R = w_{\text{shutdown}} \cdot \mathbf{1}_{\text{shutdown}} + w_{\text{seq}} \cdot \mathbf{1}_{\text{seq\_err}} + w_{\text{param}} \cdot \mathbf{1}_{\text{param\_err}} + w_{\text{heal}} \cdot \mathbf{1}_{\text{repaired}}$$

---

## 4. Tier 2: Self-Healing Validation Loop (`SIFT_MUTATION_RETRIES = 3`)

To guarantee 0% syntax-level crashes reaching the physical testbed, every proposed mutation must pass through an automated self-healing validation filter:

```
[ Proposed Mutation ] ──► [ Local Pydantic / Codec Validator ]
                                      │
                     ┌────────────────┴────────────────┐
                     │ Valid                           │ Exception Thrown
                     v                                 v
          [ Forward to Judge ]              [ Extract Traceback & AST ]
                                                       │
                                                       v
                                            [ Feed back to Mutator ]
                                            (Attempt <= 3)
```

If the candidate throws a `ValidationError` or serialization crash:
1. The exact exception traceback, schema location, and field name are isolated.
2. A repair prompt or programmatic patch is constructed.
3. The mutation is re-evaluated. If all 3 attempts fail, a safe canonical fallback is returned.

---

## 5. Tier 3: SIFT Pairwise Judge with Bradley-Terry Tournament

When the bandit spawns a pool of $N = 3$ candidate mutations, we run a round-robin tournament to select the Pareto-optimal test vector.

For each pair of candidates $(i, j)$, the judge evaluates preference based on **Safety Criticality** and **Stealth**:
$$y_{ij} \in \{A, B, \text{TIE}\}$$

The latent strength $\theta_i$ of each candidate is solved via the Bradley-Terry model:
$$P(i \succ j) = \frac{e^{\theta_i}}{e^{\theta_i} + e^{\theta_j}}$$

We solve for $\mathbf{\theta}$ using the Iterative Minorization-Maximization algorithm:
$$p_i^{(k+1)} = \frac{W_i}{\sum_{j \neq i} \frac{N_{ij}}{p_i^{(k)} + p_j^{(k)}}}$$
$$\theta_i = \ln(p_i) - \frac{1}{N}\sum_{k=1}^N \ln(p_k)$$

The candidate with $\max(\theta_i)$ is awarded the dispatch slot to the physical charging station.

---

## 6. Procedural Decision Graph & Parameter Evolution

The operational lifecycle is structured as an executable node graph (`procedural_graph/`):

```
Node_StateInspection ──► Node_BanditSelect ──► Node_CandidateGen
                                                       │
                                                       v
Node_HardwareDispatch ◄── Node_SIFTJudge ◄── Node_SelfHealing
         │
         v
Node_OracleEvaluate ──► Node_BanditUpdate ──► [ Graph Mutator ]
```

The **Graph Mutator** monitors long-term telemetry:
* If $N_{\text{pulls}}(a_i) / t > 0.70$ (over-exploitation), it increases $c$ by 25%.
* If the average reward drops below 2.0, it dynamically expands candidate pool size from 3 to 5.
* This realizes a truly **self-improving fuzzing policy**.
