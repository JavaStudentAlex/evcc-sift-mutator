# `connection/` — Vendored real EVCC connection stack

This package is **vendored** (copied) from the hackathon's reference EVCC
simulator and provides the real, hardware-verified transport that lets
EVCC-SIFT-Mutator drive an actual charging station (SECC) rather than only the
in-process `SeccMockServer`.

## Provenance

| | |
|---|---|
| **Upstream** | https://github.com/eMobMarkus/evcc_simulator |
| **Vendored commit** | `c884950` |
| **Vendored on** | 2026-09-27 |
| **Context** | YFN x EU Energy Hackathon 2026 — EnBW track ("AI-Assisted Penetration Testing of Fast-Charging Infrastructure"), shared by the organizers as an example to build on |

### What was changed on vendoring

The initial copy rewrote intra-package imports from upstream's flat layout to
this package. Integration also adds finite, noninteractive CurrentDemand runs,
response and frame validation, explicit failure propagation, and socket/CP cleanup
on errors. The upstream provenance headers identify the starting revision, not
an assertion that every file remains identical to it. Existing empirical
CAN-trace notes in `can_control.py` come from upstream; this repository's bridge
has been tested with offline fakes, not independently verified on a charger.

Not vendored: upstream's `main.py`, `manual_control.py`, CAN diagnostic scripts,
`decode_pcap.py`, tests, and installers. Their responsibilities are re-created in
this repo's `run_fuzzer.py` (hardware mode) and `simulator/real_transport.py`
(the typed↔DIN bridge).

## ⚠️ Licensing

The upstream repository **carries no `LICENSE` file**. It was published as a
hackathon reference for participants to build on, so vendoring is consistent with
its intent, but the license terms are unconfirmed. **Before any use beyond the
hackathon, confirm licensing with the upstream author / the EnBW track
organizers.** This repo's own `pyproject.toml` declares MIT for the
*EVCC-SIFT-Mutator* code; that declaration does **not** cover the vendored files
in this directory.

## ⚠️ Authorized testing only

This stack talks directly to the SECC of a real charging station and can send
state-altering and deliberately malformed frames. Use it **only against your own
test bench with explicit authorization**, never against third-party or
production infrastructure. This mirrors the warning in the upstream README.

## Modules

| Module | Layer | Optional dependency |
|---|---|---|
| `config.py` | `EvccConfig` / `NetworkConfig` / `CanConfig`, `local_config.json` loader | — |
| `can_control.py` | CP signal generator + PP/R2/R3 resistor control over ixxat CAN | `python-can` (+ ixxat VCI driver) |
| `slac.py` | EV-side SLAC (HomePlug Green PHY) network join | `scapy` + `pyslac` |
| `sdp.py` | SECC Discovery Protocol (UDP multicast) | — |
| `session.py` | `EVCCSession` — V2GTP/TCP(/TLS) transport + raw-byte hooks | — |
| `codec.py` | DIN SPEC 70121 EXI encode/decode | `iso15118` + Java runtime |
| `hooks.py` | Raw-byte `pre_send` / `post_receive` fuzzing hooks | — |
| `state_machine.py` | Verified DIN SPEC 70121 DC session sequencer | (uses `codec` + `session`) |

`import connection` works without the optional hardware dependencies. Importing
`connection.codec` initializes the EXI backend and can start Java. Check
`connection.capability_summary()` without starting it, or
`connection.exi_available()` to check actual codec availability.

## Benchmark runner

The hardware CLI uses the vendored DIN sequencer with `AIMutationCodec` at the
pre-EXI boundary. It checks hardware dependencies and EXI availability before
opening CAN. It does not use the mock codec as a substitute for real EXI.

```bash
uv sync --extra dev --extra hardware
uv run python run_fuzzer.py --mode hardware \
  --config connection/local_config.example.json \
  --cycles 1 --current-demand-cycles 3 --no-mutations \
  --report reports/hardware-baseline.md
```

Configure the actual network interface, CAN backend/channel and fixture bitrate
before running. The example describes SocketCAN on `can0`; IXXAT uses
`"bustype": "ixxat"` with a numeric channel. The reference analyzer bus uses
1000000 bit/s, independently of any vehicle telemetry bus. A local
`connection/local_config.json` is loaded automatically and is ignored by Git.
Explicit CLI values override the JSON configuration. No interactive setup is
required.

Omit `--no-mutations` to select numerical/semantic test cases for nonterminal
`CurrentDemandReq` frames. The final `ChargingComplete` frame is preserved.
`--mutation-targets` can also select `PreChargeReq` and
`ChargeParameterDiscoveryReq`; rejection there may end a session before
CurrentDemand. Each iteration closes the socket and returns CP/PP to idle,
including on setup errors or interruption. A cleanup failure stops further runs.

Hardware reports contain measured session outcomes and mutation observations in
Markdown plus a sibling JSON file. Expected rejection, a timeout, and an
established defect are different outcomes; the report does not infer CVEs.
Exit codes: 0 all sessions completed, 1 one or more failed, 2 setup failed,
130 interrupted. Completion describes the protocol session, not a conformance
certification.

For software simulation and Colab, start with [SIMULATION.md](../SIMULATION.md).
The hardware runner requires CP/PP and SLAC; connecting directly to a standalone
Josev or EVerest SECC still needs a software-only connection path.
