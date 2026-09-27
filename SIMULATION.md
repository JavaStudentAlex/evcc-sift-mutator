# Simulation-first mutation benchmarking

This project tests charging-protocol implementations on authorized, isolated
fixtures. Start with an offline baseline; only move to networked peers after
the same test vectors and observations are reproducible locally. A rejected
request, closed connection, or simulated interlock is an observation, not
evidence of a defect or a physical consequence.

## Available now

| Fixture | Exercises | Does not exercise |
| --- | --- | --- |
| `simulator.secc_mock.SeccMockServer` | In-process request/response sequence, session state, parameter rejection, virtual contactor state | Real DIN EXI, V2GTP/TCP, SDP, IPv6, SLAC, CAN/CP/PP, physical relays |
| `simulator.state_machine.Iso15118EvccStateMachine` | Offline, finite EVCC handshake against the mock | Vendored DIN wire sequencer in `connection.state_machine` |
| `notebooks/mutation_benchmark.ipynb` | Seeded, independent one-mutation sessions with canonical control, actual sent request and received response | Network interoperability, charger conformance certification, model inference |

Run the existing mock CLI:

```bash
uv run pytest
uv run python run_fuzzer.py --mode mock --cycles 5 --report reports/mock_findings.md
```

The CLI's current mock report is a *search/telemetry demonstration*, not a
conformance verdict. Its graph sends a mutation inside the pre-send hook, then
returns the original request for the state machine to send again. That can
confound attribution of the reported session outcome. Use the notebook's
single-dispatch trials for a measured offline baseline until the runner is
corrected. The protocol model's `to_exi_payload()` returns JSON-encoded bytes,
not standards-compliant EXI; real EXI is provided by `connection.codec` when
the optional codec and Java runtime are available.

## Next fixture

| Candidate | Evidence | Fit and limits |
| --- | --- | --- |
| [EcoG Josev Community (`iso15118`)](https://github.com/ecog-io/iso15118) | Its README documents both SECC and EVCC, local `make run-secc` / `make run-evcc`, Docker `make build` / `make dev`, Java for the EXIficient codec, and IPv6 link-local networking. | **Recommended next independent SECC** for DIN/ISO wire-level interoperability tests. Integrating this project's vendored EVCC still requires an isolated network and deliberate treatment of its CAN/SLAC/CP bring-up; running two separate stacks does not automatically emulate a physical test bench. |
| [EVerest](https://github.com/EVerest/EVerest) | Modular, MQTT-based charger stack with DIN SPEC 70121 and ISO 15118 support. Its [DC software-in-the-loop config](https://github.com/EVerest/EVerest/blob/main/config/config-sil-dc.yaml) lists `DCSupplySimulator`, `YetiSimulator`, `SlacSimulator`, and `IMDSimulator`. | Good later system-level station fixture. More moving parts and an integration layer; the presence of a simulated SLAC module does not establish compatibility with the vendored raw-Ethernet SLAC driver. |
| [Local `SeccMockServer`](simulator/secc_mock.py) | Built into this repository, no device or service required. | Fast deterministic component baseline when seeded; simplified station behavior is not an independent interoperability oracle. |

Josev's documented Docker setup has an isolated IPv6 network for its
containers. Its README notes that a host trying to reach link-local
addresses needs an appropriate network topology (such as host networking);
do not assume a containerized SECC is reachable from the vendored EVCC without
configuring the network. Confirm protocol mode and interface before comparing
DIN responses. Keep hardware mode on a separately authorized test bench.

## Colab and the model proxy

[Colab](https://colab.research.google.com/) can run the Python-only notebook
against a cloned checkout. It is a convenient place for repeated mock trials,
tables, and saved JSON observations. The notebook installs only the project's
`dev` extra when run in hosted Colab. Uncommitted local changes must be pushed
or transferred before a remote clone can see them. A hosted notebook cannot
assume access to your laptop's `localhost`, raw Ethernet, CAN device, or
link-local multicast network. Use a local notebook runtime with the correct
network and authorization for those later integration stages.

[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) is a gateway to
language models, **not a charger or a protocol simulator**. No proxy or model
is required for the notebook: mutation operators and ranking in the current
offline path are Python implementations. The optional diagnostic cell sends
only `GET /v1/models` if `CLIPROXYAPI_BASE_URL` is explicitly set. It does
not send prompts, payloads, credentials to notebook output, or test vectors
to a model. A locally running proxy at `127.0.0.1:8317` is reachable from
the local machine/runtime, not automatically from hosted Colab.

## Benchmark contract

For every test case, record seed, operator, base request, *one* transmitted
mutation (or validated fallback), received response code, session completion,
and simulator observations. Reset the mock between cases. Keep a nominal
control alongside mutated cases, and distinguish expected rejection from an
actual unexpected response. Compare runs on identical seeds and fixture
versions; do not treat selection reward, response rejection, or synthetic
severity as a conformance score. The notebook follows this contract for its
current-demand trials and optionally exports a JSON artifact.
