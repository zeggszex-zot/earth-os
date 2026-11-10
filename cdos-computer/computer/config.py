"""Settings.  Defaults here; override any of them in config.toml (see config.example.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    name: str = "CDOS"                     # the computer's name
    user: str = "user"                     # what the computer calls you
    corpus: str = str(HERE / "data")       # the cdos-web data/ folder
    corpus_integrity: str = "verify"       # "verify": check meta.json's content hash against the shards at load and
                                           #   flag a mismatch in every answer | "strict": also refuse to answer from
                                           #   a corpus that fails | "trust": skip the check (answers say "trusted")
    method: str = "coins"                  # "coins" or "yarrow"
    knowledge_aside: bool = True           # sampler output may add one known fact about a named thing

    # Mini LM (llama.cpp llama-server).  Empty endpoint = no model; the computer still answers.
    llm_endpoint: str = "http://127.0.0.1:8080"
    llm_mode: str = "precall"              # "precall" (computer runs the sampler, LM words it), "tools", or "off"
    llm_max_words: int = 70
    llm_timeout: float = 45.0

    # Navigator (computer/navigator.py): multi-step questions answered by a checked plan over the corpus.
    navigator: str = "auto"                # "auto": chains, comparisons, yes/no, reverse...; "always"; "off"
    navigator_planner: str = "rules"       # "rules" | "lm" (model plans) | "propose" (model plans and words the
                                           #   answer; the executor checks both) | "direct" (model answers from
                                           #   its weights; the rules' run checks it).  Rules are always the fallback.
    navigator_endpoint: str = ""           # planner model server; empty = llm_endpoint
    navigator_fewshot: bool = True         # False for a model fine-tuned with tools/make_navigator_data.py
    relation_lexicon: str = ""             # learned word -> relation mappings (tools/learn_lexicon.py), e.g. "lexicon.json"
    navigator_controller: str = ""         # the learned controller (tools/train_controller.py), e.g. "controller.json":
                                           #   tried after the rules, for question shapes they have no pattern for
    navigator_controller_min_p: float = 0.08   # its lowest score worth trying (raise it for fewer, surer answers)
    neural_registry: str = ""              # the neural core's model registry (python3 -m computer.neural), e.g.
                                           #   "models/neural": its active model plans after the rules.  Empty = off.
                                           #   A missing, corrupt or incompatible model is skipped, never fatal.
    neural_min_p: float = 0.08             # the neural planner's lowest score worth trying
    neural_experience: bool = True         # append each prediction and its checked outcome to
                                           #   <neural_registry>/experience/experience.jsonl (never read when answering)
    growth_approval: str = "manual"        # the Growth Engine's promotion policy: "manual" is the only one there is
    growth_auto_run: bool = True           # cdos-growth.timer also trains and evaluates tasks a person has answered
    growth_heldout: list = field(default_factory=list)      # hand-written held-out questions (JSONL) a candidate
                                                            #   is evaluated on, as in python3 -m computer.neural evaluate
    growth_regression: list = field(default_factory=list)   # questions the rules must keep getting right (JSONL)
    plugins_dir: str = ""                  # capability plugins (computer/plugins.py), e.g. "plugins"; empty = none

    # Robotics, simulation only (computer/robotics): sensor state in, a checked candidate motion out.
    robotics: bool = False                 # the robot controller in the API service
    robot_safety_file: str = ""            # the safety envelope; empty = computer/data/robot_safety.json
    robotics_journal: str = ""             # append-only decision journal, e.g. "robotics/decisions.jsonl"; empty = none

    # The Governor (computer/governor.py): who may do what, the persistent emergency stop, the audit of decisions.
    governor_policy: str = ""              # roles file, e.g. "/etc/cdos-computer/governor.json" (root-owned).  Empty:
                                           #   nobody holds a privileged role (no clearing the stop, no promotion)
    governor_dir: str = ""                 # audit.jsonl, its head record and estop.json, e.g. "/var/lib/cdos/governor".
                                           #   Empty: kept in memory, and the stop is engaged at every start

    # The local API (computer/services/api.py): a Unix socket, or TCP on the loopback address only.
    api_socket: str = ""                   # e.g. "/run/cdos/api.sock"
    api_tcp_port: int = 0                  # e.g. 8765 (127.0.0.1 only); 0 = off
    rules_file: str = ""                   # pathways and knowledge rules; empty = computer/data/rules.json (python3 -m computer.rules checks one)

    # Compute engine (computer/compute.py): how hard the machinery works, and on what.
    compute_profile: str = "balanced"      # "eco" | "balanced" | "priority" | "auto" (eco when hot or busy)
    compute_backend: str = "auto"          # "auto" (a validated CCPU, else python) | "python" | "numpy" | "ccpu"
    compute_ccpu_driver: str = ""          # your FPGA driver, "module:Class" (see CCPUBackend)
    compute_hot_c: float = 70.0            # auto: eco at or above this CPU temperature
    compute_cool_c: float = 62.0           # auto: back to balanced below this
    controller_precision: str = "float"    # "float" | "int8" | "int4": the controller's weights in memory
    llm_endpoint_priority: str = ""        # priority profile: a bigger model's server (e.g. on a NUC); empty = llm_endpoint

    # Analysis (computer/analysis.py): estimates from stated quantities and corpus figures, e.g. battery runtime.
    analysis: bool = True                  # False: no estimates; such questions go to knowledge as before
    analysis_efficiency: float = 0.85      # the conversion efficiency assumed when none is given (always said)
    diagnostics: bool = True               # "what is the processor temperature?", "system status"
    # Working state (computer/state.py): the open calculation survives a restart.  Empty = kept in memory only.
    state_file: str = ""                   # e.g. "computer.state.json"
    state_max_age_minutes: float = 60      # an older saved calculation is no longer a follow-up's context

    crisis_line: str ="In the US you can call or text 988, any time."

    # Body
    voice: str = "auto"                    # "piper", "espeak", "print" or "auto"
    piper_model: str = str(HERE / "voices" / "en_US-lessac-low.onnx")
    vosk_model: str = str(HERE / "models" / "vosk-model-small-en-us-0.15")
    mic_device: str = ""                   # ALSA device for arecord, e.g. "plughw:1,0"; empty = default
    gpio_button: int = 17                  # press and hold to talk
    gpio_tilt: int = 27                    # tilt/shake switch: shake to cast
    gpio_eye_left: int = 12                # PWM pins for the two status LEDs
    gpio_eye_right: int = 13
    idle_saying_minutes: float = 0         # >0: output a stored aphorism after this much idle time
    log_file: str = str(HERE / "computer.log.jsonl")


def load(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    candidates = [path] if path else [os.environ.get("CDOS_COMPUTER_CONFIG") or os.environ.get("CDOS_OWL_CONFIG"), HERE / "config.toml",
                                      Path("/etc/cdos-computer/config.toml")]
    for p in candidates:
        if p and Path(p).is_file():
            with open(p, "rb") as fh:
                data = tomllib.load(fh)
            known = {f.name for f in fields(Config)}
            for k, v in data.items():
                if k in known:
                    setattr(cfg, k, v)
            break
    return cfg
