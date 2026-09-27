"""Finite mutation benchmark sessions over the vendored DIN connection stack."""
from __future__ import annotations

import logging
from dataclasses import asdict

from connection.config import EvccConfig
from oracles.benchmark_reporter import write_benchmark_report
from oracles.error_detector import ChargerErrorDetector
from procedural_graph.graph_runtime import ProceduralFuzzingGraph
from simulator.real_transport import AIMutationCodec, DEFAULT_TARGET_REQUEST_NAMES, HardwareFuzzEngine

logger = logging.getLogger(__name__)
SUPPORTED_TARGETS = {"CurrentDemandReq", "PreChargeReq", "ChargeParameterDiscoveryReq"}


def run_hardware_session(
    *,
    cfg: EvccConfig,
    cycles: int,
    report_path: str,
    current_demand_cycles: int = 3,
    mutations_enabled: bool = True,
    mutation_targets: set[str] | None = None,
    verbose: bool = False,
) -> int:
    """Run independent sessions; return 0 complete, 1 failed, 2 setup, 130 interrupted.

    Optional hardware imports occur only on this path. Validation and EXI
    availability are checked before opening the CAN bus or changing CP/PP.
    """
    targets = set(DEFAULT_TARGET_REQUEST_NAMES if mutation_targets is None else mutation_targets)
    metadata = {
        "mode": "hardware",
        "protocol": "DIN SPEC 70121",
        "configured_cycles": cycles,
        "current_demand_cycles": current_demand_cycles,
        "mutations_enabled": mutations_enabled,
        "mutation_targets": sorted(targets),
        "configuration": asdict(cfg),
    }
    sessions: list[dict] = []
    observations: list[dict] = []
    try:
        if cycles < 1 or current_demand_cycles < 1:
            raise ValueError("Session and CurrentDemand cycle counts must be positive")
        if targets - SUPPORTED_TARGETS:
            raise ValueError(f"Unsupported mutation targets: {sorted(targets - SUPPORTED_TARGETS)}")
        if cfg.can.bitrate <= 0:
            raise ValueError("CAN bitrate must be positive")
        plc_mac = bytes.fromhex(cfg.network.plc_modem_mac_hex)
        if len(plc_mac) != 6:
            raise ValueError("PLC modem MAC must contain six bytes")

        from connection import capability_summary

        missing = [name for name, available in capability_summary().items() if not available]
        if missing:
            raise RuntimeError(
                f"Missing hardware capabilities: {', '.join(missing)}. "
                "Install with uv sync --extra dev --extra hardware; EXI also requires Java."
            )

        from connection import codec
        from connection.can_control import CanControl, CanControlConfig
        from connection.hooks import HookManager
        from connection.session import EVCCSession, MessageSendError
        from connection.slac import SlacConfig, SlacTransport, get_interface_mac, run_ev_slac_matching
        from connection.state_machine import Iso15118EvccStateMachine

        if not codec.is_compliant():
            raise RuntimeError("A working DIN EXI codec and Java runtime are required for hardware mode")
        codec.configure_logging(verbose)
    except Exception as exc:
        logger.exception("Hardware setup failed")
        metadata.update(status="setup_error", error=str(exc), attempted_sessions=0, successful_sessions=0)
        write_benchmark_report(report_path, metadata, sessions, observations)
        return 2

    graph = ProceduralFuzzingGraph()
    detector = ChargerErrorDetector()
    engine = HardwareFuzzEngine(graph, detector)
    mutations_applied = 0
    encoding_fallbacks = 0
    interrupted = False
    for iteration in range(1, cycles + 1):
        record = {"iteration": iteration, "completed": False, "phase": "can"}
        session = None
        can_ctrl = None
        mutation_codec = AIMutationCodec(
            engine, target_request_names=targets, enabled=mutations_enabled, codec_module=codec
        )
        try:
            can_ctrl = CanControl(CanControlConfig(
                channel=cfg.can.channel, bustype=cfg.can.bustype, bitrate=cfg.can.bitrate,
            ))
            can_ctrl.connect()
            can_ctrl.start_keep_alive()
            can_ctrl.start_feedback_listener()
            can_ctrl.set_pilot_gen_active_and_verify(True)
            can_ctrl.set_pp_state_and_verify(
                r2_ohm=cfg.can.r2_ohm, r3_ohm=cfg.can.r3_ohm,
                r2_on=True, r3_on=True, pp_state=True,
            )
            can_ctrl.wait_for_hlc_request(side="ev")

            record["phase"] = "slac"
            slac_cfg = SlacConfig(
                interface_name=cfg.network.interface_name,
                own_mac=get_interface_mac(cfg.network.interface_name),
                plc_modem_mac=plc_mac,
            )
            run_ev_slac_matching(SlacTransport(cfg.network.interface_name), slac_cfg)

            record["phase"] = "connect"
            session = EVCCSession(
                network=cfg.network, hooks=HookManager(),
                encode_fn=mutation_codec.encode, decode_fn=mutation_codec.decode,
            )
            endpoint = session.connect()
            record["endpoint"] = {
                "address": endpoint.secc_address, "port": endpoint.secc_port,
                "tls": endpoint.use_tls,
            }

            def set_cp_charging_state(charging: bool) -> None:
                can_ctrl.set_pp_state_and_verify(
                    r2_ohm=cfg.can.r2_ohm, r3_ohm=cfg.can.r3_ohm,
                    r2_on=True, r3_on=charging, pp_state=True,
                )

            record["phase"] = "session"
            sequencer = Iso15118EvccStateMachine(
                session, cfg, cp_state_callback=set_cp_charging_state, verbose=verbose,
            )
            sequencer.run_dc_session(current_demand_max_cycles=current_demand_cycles)
            record.update(completed=True, phase="completed")
        except KeyboardInterrupt:
            interrupted = True
            record["error"] = "Interrupted by user"
            mutation_codec.discard_pending()
        except Exception as exc:
            record["error"] = str(exc)
            logger.exception("Session %s failed during %s", iteration, record["phase"])
            if isinstance(exc, MessageSendError):
                mutation_codec.discard_pending(outcome="transmission_unconfirmed")
            elif isinstance(exc, (ConnectionError, TimeoutError)):
                mutation_codec.flush_crash()
            else:
                mutation_codec.discard_pending()
        finally:
            # Attempt both closes even when one fails; a cleanup failure invalidates completion.
            cleanup_errors = []
            for name, resource in (("session", session), ("can", can_ctrl)):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        cleanup_errors.append(f"{name}: {exc}")
                        logger.exception("%s cleanup failed", name)
            if cleanup_errors:
                record.update(completed=False, cleanup_errors=cleanup_errors)
            mutations_applied += mutation_codec.mutations_applied
            encoding_fallbacks += mutation_codec.encoding_fallbacks
            observations.extend(
                {**observation, "iteration": iteration}
                for observation in mutation_codec.observations
            )
            sessions.append(record)
        if interrupted or record["phase"] not in {"session", "completed"} or cleanup_errors:
            break

    successful = sum(record["completed"] for record in sessions)
    status = "interrupted" if interrupted else "completed" if successful == cycles else "incomplete"
    metadata.update(
        status=status, attempted_sessions=len(sessions), successful_sessions=successful,
        mutations_applied=mutations_applied, encoding_fallbacks=encoding_fallbacks,
        arm_distribution=graph.bandit.get_stats(),
        candidate_pool_size=graph.config["candidate_pool_size"],
    )
    write_benchmark_report(report_path, metadata, sessions, observations)
    return 130 if interrupted else 0 if successful == cycles else 1
