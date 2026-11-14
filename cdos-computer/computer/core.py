"""Core: the CDOS computer's turn loop.  Routes each input to one module and builds the result.

    KNOWLEDGE   corpus.py, navigator.py    facts from the CDOS Corpus, multi-step plans
    LOGIC       router.py, calc.py         intent + policy; arithmetic; clock; diagnostics
    ANALYSIS    analysis.py, quantity.py   unit-aware estimates (runtime, energy, power)
    SAMPLER     iching.py, instinct.py     optional random I Ching output; never evidence

Operating rules (enforced here, in code, not left to a model):

 1. Facts come only from KNOWLEDGE; arithmetic only from LOGIC.  The random
    sampler is never asked a factual question and can never change a fact or a sum.
 2. The SAMPLER answers open questions only ("should I", "what do you think").
    Its output is labelled as random, non-factual and not a prediction.
 3. Health, money and safety queries never go to the sampler, however they
    are phrased.  A person who may be in danger is directed to a human.
 4. The language model, when there is one, is an output formatter.  It may
    re-word what the computer already decided to output; a reply that adds
    numbers, drops the hexagram or predicts the future is discarded and the
    computer's template output is used instead.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from . import instinct
from . import state as state_mod
from . import envelope as envelope_mod
from .analysis import Analyst
from .capabilities import default_registry
from .clock import current_year
from .calc import spoken_expr
from .diagnostics import Diagnostics
from pathlib import Path

from .compute import PROFILES, Engine
from .config import HERE, Config
from .corpus import SOURCE_RANK, STOP, Corpus, as_context, rank_by_overlap
from .llm import TOOLS, MiniLM
from .navigator import Navigator
from .phrase import name_of, phrase_block
from .planner import ADVANCED, Context, LMPlanner, RulePlanner, navigate, normalize
from .proposer import Proposer, propose_and_verify
from .router import Route, classify
from .rules import Engine as RuleEngine, RuleError, parse_rules, run_pathway
from . import rules as rules_mod

# The actions each pathway's rules may choose (computer/data/rules.json), and the arguments each takes.
# A rule naming anything else is refused at start-up.
PATHWAY_ACTIONS = {
    "knowledge": {"navigate": set(), "lookup": set(), "say_navigator": {"which"}, "say_lookup": {"tail"},
                  "say_unreachable": set(), "say_unknown": set()},
}
HEALTH_TAIL = "NOTICE: for questions about your own body, consult a doctor, nurse or pharmacist."
MONEY_TAIL = ("NOTICE: this system does not give financial or legal advice, and does not run the sampler on it. "
              "Consult a qualified expert.")

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
PREDICTS = re.compile(r"\b(will definitely|definitely will|guarantee\w*|for sure|certainly will|100 ?%|"
                      r"you will (win|lose|fail|pass|die|succeed))\b", re.I)

SMALL_TALK = [
    (r"^(hi|hello|hey|hiya|yo|good (morning|afternoon|evening))\b", "{name} online. Hello, {user}. Ready for input.", "happy"),
    (r"\bhow are you\b|\bhow('s| is) it going\b", "All systems nominal.", "happy"),
    (r"\b(thank you|thanks|cheers)\b", "Acknowledged.", "happy"),
    (r"\b(who|what) are you\b|\byour name\b",
     "Identity: {name}. Type: deterministic reasoning computer. Modules: KNOWLEDGE (corpus facts), LOGIC "
     "(arithmetic and rules), ANALYSIS (unit-aware estimates), SAMPLER (random I Ching output, non-factual).", "neutral"),
    (r"\bwhat can you do\b|^help\b",
     "Supported input: fact queries (example: how many moons does Jupiter have), arithmetic, battery and power "
     "estimates, system status. 'Should I' queries run the random sampler.", "neutral"),
    (r"^(bye|goodbye|good night|see you|later)\b", "Session ended. Goodbye, {user}.", "happy"),
    (r"^(ok|okay|cool|nice|great)\b", "Acknowledged.", "happy"),
]

PERSONA = (
    "You are {name}, a CDOS reasoning computer. You output to {user} in one to three short sentences: "
    "precise, neutral, technical. No lists, no markdown, no emoji, no feelings.\n"
    "You will be given NOTES from your own modules. Output what the NOTES say, in your own words.\n"
    "Rules: keep the hexagram's name exactly. Do not add facts, numbers, names or places that are not in the "
    "NOTES. Never say what will happen in the future: the sampler output is random, not a prediction. "
    "If the NOTES contain a fact, repeat it exactly."
)

TOOL_PERSONA = (
    "You are {name}, a CDOS reasoning computer. Answer {user} in one to three short, precise sentences. For any "
    "fact, call lookup_fact and only repeat what it returns. For open, personal or 'should I' questions, call "
    "consult_iching and report its output as a random, non-factual sample, naming the hexagram. Never predict "
    "the future."
)


@dataclass
class Reply:
    text: str
    mood: str = "neutral"
    faculty: str = "logic"          # knowledge | logic | instinct | care
    voice: str = "computer"         # "computer" (templates) or the LM's label
    route: Route | None = None
    perspective: instinct.Perspective | None = None
    trace: list = field(default_factory=list)
    ms: int = 0
    compute: dict = field(default_factory=dict)     # profile, wall/CPU time, energy, LM timings (computer/compute.py)
    evidence: dict = field(default_factory=dict)    # analysis answers: inputs with provenance, steps, assumptions
    envelope: dict = field(default_factory=dict)    # the result envelope (computer/envelope.py): truth, certainty, evidence ...


def ungrounded_numbers(reply: str, *allowed_texts: str) -> list[str]:
    allowed = {n.replace(",", "") for t in allowed_texts for n in NUMBER.findall(t or "")}
    bad = []
    for raw in NUMBER.findall(reply):
        n = raw.replace(",", "")
        if n in allowed or len(n) == 1:
            continue
        bad.append(raw)
    return bad


class Computer:
    def __init__(self, cfg: Config, corpus: Corpus | None = None, llm: MiniLM | None = None):
        self.cfg = cfg
        self.corpus = corpus if corpus is not None else Corpus(cfg.corpus, integrity=cfg.corpus_integrity).load()
        self.llm = llm if llm is not None else (MiniLM(cfg.llm_endpoint, timeout=cfg.llm_timeout)
                                               if cfg.llm_endpoint and cfg.llm_mode != "off" else None)
        self.last: instinct.Perspective | None = None
        self.rules_error = ""
        self.knowledge, self.pathways = self._load_rules(cfg.rules_file)
        # Plugins first (computer/plugins.py): each one is checked on its own and refused, never fatal, if it fails.
        self.plugins, extra = self._load_plugins()
        # Every capability's declaration is checked against the code here: a bad built-in one stops start-up.
        try:
            self.registry = default_registry(cfg, extra)
        except ValueError as e:
            if not extra:
                raise
            for p in self.plugins:              # can't happen if each passed alone; if it does, no plugin is used
                p.error, p.component = p.error or f"refused with the others: {e}", None
            self.registry = default_registry(cfg)
        # The shared neural interface (computer/neural/components.py): plugin components now, the neural core's
        # selector once it loads.
        from .neural.components import ComponentRegistry
        self.components = ComponentRegistry()
        for p in self.plugins:
            if p.component is not None:
                self.components.register(p.component)
            elif p.name:
                self.components.refuse(p.name, p.error)
        self.navigator = Navigator(self.corpus, knowledge=self.knowledge, registry=self.registry)
        self._said_by = ""
        self._last_lookup = None
        self.context = Context()           # the last answered question, for "another one", "what about sad"
        self.rules = RulePlanner(self.corpus, self.context)
        self.planners = [self.rules]
        self.proposer: Proposer | None = None
        ep = cfg.navigator_endpoint or cfg.llm_endpoint
        if ep and cfg.navigator_planner in ("lm", "propose", "direct"):
            lm = self.llm if (self.llm and ep == cfg.llm_endpoint) else MiniLM(ep, timeout=cfg.llm_timeout)
            if cfg.navigator_planner == "lm":
                self.planners.insert(0, LMPlanner(lm, self.corpus, fewshot=cfg.navigator_fewshot))
            else:
                self.proposer = Proposer(lm, self.corpus, fewshot=cfg.navigator_fewshot,
                                         direct=cfg.navigator_planner == "direct")
        self.controller_error = ""
        self.compute_error = ""
        try:
            self.engine = Engine.from_config(cfg)
        except ValueError as e:                  # a typo in compute_profile mustn't stop start-up
            self.compute_error = f"{e}: using balanced"
            self.engine = Engine("balanced", cfg.compute_backend, cfg.controller_precision, cfg.compute_ccpu_driver)
        self.profile = PROFILES["balanced"]
        self.llm_priority = (MiniLM(cfg.llm_endpoint_priority, timeout=cfg.llm_timeout)
                             if cfg.llm_endpoint_priority and cfg.llm_mode != "off" else None)
        self._lm_calls: list[dict] = []
        if cfg.relation_lexicon:
            from .lexicon import Lexicon
            path = Path(cfg.relation_lexicon)
            try:
                self.corpus.lexicon = Lexicon.load(path if path.is_absolute() else HERE / path)
            except (OSError, ValueError) as e:
                self.controller_error = f"lexicon not loaded: {e}"
        if cfg.navigator_controller:
            from .controller import ControllerPlanner
            path = Path(cfg.navigator_controller)
            path = path if path.is_absolute() else HERE / path
            try:
                model = self.engine.load_controller(path)
                self._probe(model)
                self.planners.append(ControllerPlanner(self.corpus, model, min_p=cfg.navigator_controller_min_p))
            except Exception as e:  # noqa: BLE001 - a damaged controller file mustn't stop start-up (or a later turn)
                self.controller_error = f"controller not loaded: {e}"
        self.neural = None                 # the neural core's planner (computer/neural), when a model is active
        self.neural_error = ""
        self.neural_dir = None
        self.experience = None             # its append-only experience log
        if cfg.neural_registry:
            self._load_neural()
        self.last_navigation = None        # (plan, result) of the last navigated question, for --plan / logs
        self.last_faculty = ""
        self.analyst = Analyst(self.corpus, efficiency=cfg.analysis_efficiency) if cfg.analysis else None
        self.diagnostics = Diagnostics(corpus=self.corpus, engine=self.engine) if cfg.diagnostics else None
        self.state_path = (Path(cfg.state_file) if Path(cfg.state_file).is_absolute() else HERE / cfg.state_file) \
            if cfg.state_file else None
        self.state_error = ""
        if self.state_path and self.analyst:
            saved = state_mod.load(self.state_path, max_age=cfg.state_max_age_minutes * 60)
            if saved and saved.get("analysis"):
                try:
                    self.analyst.restore(saved["analysis"])
                except (ValueError, KeyError, TypeError) as e:
                    self.state_error = f"saved state not used: {e}"

    def _load_plugins(self) -> tuple[list, list]:
        """(plugins, the declarations of those that passed).  Never raises."""
        if not self.cfg.plugins_dir:
            return [], []
        path = Path(self.cfg.plugins_dir)
        path = path if path.is_absolute() else HERE / path
        try:
            from .plugins import load_plugins
            plugins = load_plugins(path)
        except Exception as e:  # noqa: BLE001 - a broken plugin loader is a missing feature, not a stopped computer
            from .plugins import Plugin
            return [Plugin(path, error=f"plugins not loaded: {type(e).__name__}: {e}")], []
        return plugins, [p.declaration for p in plugins if not p.error and p.declaration]

    @staticmethod
    def _probe(model) -> None:
        """Run a loaded controller once on every feature row it has, so a file that would fail at answer time (a row
        of the wrong length, scores that aren't numbers) is refused now instead."""
        try:
            from .neural.model import probe
        except ImportError:                  # the neural package is optional; without it, load as before
            return
        probe(model, list(model.actions))

    def _load_neural(self) -> None:
        """The neural core's active model, or the reason there isn't one.  Never raises: without a model the computer
        answers with the rules, exactly as it does when neural_registry is empty."""
        root = Path(self.cfg.neural_registry)
        root = root if root.is_absolute() else HERE / root
        try:
            from .neural.runtime import NeuralPlanner, NeuralUnavailable, load_active
            try:
                net, manifest, self.neural_dir = load_active(root, corpus_base=self.corpus.base)
                if self.cfg.controller_precision in ("int8", "int4"):
                    from .compute import QuantNetwork
                    net = QuantNetwork.from_network(net, 8 if self.cfg.controller_precision == "int8" else 4,
                                                    self.engine.backend_impl)
                    self._probe(net)
                planner = NeuralPlanner(self.corpus, net, manifest, min_p=self.cfg.neural_min_p)
                from .neural.components import SelectorComponent
                self.components.register(SelectorComponent(planner))
                self.neural = planner
                self.planners.append(planner)
            except NeuralUnavailable as e:
                self.neural_error = f"neural core not loaded: {e.reason}"
        except Exception as e:  # noqa: BLE001 - nothing about the neural core may stop start-up
            self.neural, self.neural_error = None, f"neural core not loaded: {type(e).__name__}: {e}"
        if self.neural is None:
            self.registry.disable("planner.neural", self.neural_error or "not loaded")
            self.registry.disable("neural.experience", "no neural model")
            return
        if self.cfg.neural_experience:
            try:
                from .neural.experience import ExperienceLog
                self.experience = ExperienceLog(root / "experience" / "experience.jsonl")
            except Exception as e:  # noqa: BLE001 - no log is a lost record, not a lost answer
                self.experience = None
                self.neural_error = f"experience log off: {e}"
        if self.experience is None:
            self.registry.disable("neural.experience", self.neural_error or "neural_experience = false")

    def _experience(self, r: Reply) -> None:
        """One line in the neural experience log for a turn the neural planner was asked about.  Never stops a turn."""
        if self.experience is None or self.neural is None or self.neural.last is None:
            return
        try:
            from .neural.experience import turn_record
            plan, res = self.last_navigation or (None, None)
            said_by = self._said_by if r.faculty == "knowledge" else r.faculty
            self.experience.append(turn_record(self.neural, r.trace, plan, res, r.envelope, said_by))
        except Exception as e:  # noqa: BLE001
            self.experience.error = f"experience log not written: {e}"

    def _load_rules(self, path: str):
        """Knowledge rules and pathways, every rule checked.  A broken rules_file falls back to the built-in one."""
        def build(p):
            r = rules_mod.load(p)
            pw = {}
            for name, acts in PATHWAY_ACTIONS.items():
                if name not in r["pathways"]:
                    raise RuleError(f"no {name!r} pathway")
                pw[name] = RuleEngine(parse_rules(r["pathways"][name], acts))
            return r["knowledge"], pw
        if path:
            p = Path(path)
            try:
                return build(p if p.is_absolute() else HERE / p)
            except (OSError, ValueError) as e:
                self.rules_error = f"rules_file not used ({e}): using the built-in rules"
        return build(None)

    # ------------------------------------------------------------------ the turn

    def think(self, text: str, shake: bytes = b"") -> Reply:
        t0 = time.monotonic()
        text = " ".join((text or "").split())
        if not text and shake:
            text = "give me a reading"           # a shake of the device is an input too
        if not text:
            return Reply("NO INPUT.", "curious")
        tok = self.engine.meter.start()
        self.registry.begin_turn()
        self._said_by, self._last_lookup = "", None
        if self.neural is not None:
            self.neural.last = None            # set again only if this turn asks the neural planner
        self.profile, why = self.engine.choose()
        self._lm_calls = []
        route = classify(text)
        trace = [f"route: {route.intent} ({route.faculty}, {route.conf:.2f}) {route.why}",
                 f"compute: {self.profile.name} ({why})"]
        if (route.intent == "more" and self.last_faculty == "knowledge" and self.cfg.navigator != "off"
                and self.rules._followup(normalize(text))):
            # "tell me more" / "what does it mean" after a knowledge answer follows that answer, not the sampler
            route = Route("fact", "knowledge", route.conf, "follow-up to the last answer (not the sampler)")
            trace.append(f"route: {route.why}")

        analysed = None
        if self.analyst and route.faculty != "care" and not route.sensitive and route.intent != "status":
            analysed = self.analyst.handle(text, route.intent)
        if analysed is not None:
            route = Route("analysis", "logic", 0.9, f"analysis: {analysed.kind}", also=[route.intent])
            r = Reply(analysed.text, "thinking" if analysed.kind == "clarify" else "happy", "logic",
                      trace=analysed.trace, evidence=analysed.finding.to_json() if analysed.finding else {})
        elif route.faculty == "care":
            r = self._care()
        elif route.intent in ("smalltalk", "clock", "calc", "unavailable", "status"):
            r = self._logic(text, route)
        elif route.faculty == "instinct":
            r = self._instinct(text, route, shake, trace)
        else:
            r = self._knowledge(text, route, trace)

        if analysed is None and self.analyst:
            self.analyst.idle()
        if self.analyst and self.analyst.changed:
            if self.registry.choose("state.working") == "state.working" and self.state_path:
                if not state_mod.save(self.state_path, {"analysis": self.analyst.to_json()}):
                    self.state_error = f"couldn't write {self.state_path}"
        r.route, r.trace = route, trace + r.trace
        r.envelope = self._envelope(r, route, analysed)
        if self.cfg.corpus_integrity == "strict" and not getattr(self.corpus, "integrity_ok", True) \
                and (r.faculty == "knowledge" or r.envelope.get("evidence")):
            r = self._integrity_refusal(r, route)
        r.ms = int((time.monotonic() - t0) * 1000)
        r.compute = {"profile": self.profile.name, **self.engine.meter.stop(tok, self._lm_calls or None)}
        self.last_faculty = r.faculty
        self._experience(r)
        self._log(text, r)
        return r

    # ------------------------------------------------------------------ faculties

    def _care(self) -> Reply:
        return Reply("PRIORITY: this may involve someone's safety. Please talk to a person you trust right now, "
                     f"or call your local emergency number. {self.cfg.crisis_line} "
                     "This is a computer; you deserve a real person's help.", "concerned", "care")

    def _logic(self, text: str, route: Route) -> Reply:
        if route.intent == "calc":
            c = route.calc
            return Reply(f"{spoken_expr(c['expr'])} is {c['text']}.", "happy", "logic")
        if route.intent == "clock":
            now = time.localtime()
            return Reply(time.strftime("TIME: %-I:%M %p, %A, %B %-d.", now), "happy", "logic")
        if route.intent == "status":
            got = self.diagnostics.answer(text) if self.diagnostics else None
            if got:
                said, how = got
                return Reply(said, "happy", "logic", trace=how)
            return Reply("UNAVAILABLE: that value cannot be read on this machine.", "concerned", "logic")
        if route.intent == "unavailable":
            return Reply("UNSUPPORTED: this system has no device control or internet access.", "concerned", "logic")
        for pat, answer, mood in SMALL_TALK:
            if re.search(pat, text, re.I):
                return Reply(answer.format(user=self.cfg.user, name=self.cfg.name), mood, "logic")
        return Reply("Acknowledged.", "happy", "logic")

    def _navigate(self, text: str, route: Route, trace: list) -> tuple[Reply | None, Reply | None]:
        """(reply now, reply to fall back on).  In "auto" the navigator answers the questions the
        one-shot lookup can't (chains, comparisons, yes/no, reverse, explain, counts); simple ones
        stay with the lookup, and the navigator's answer is kept in case the lookup comes up empty."""
        mode = self.cfg.navigator
        if mode == "off" or not self.corpus.ready or route.intent not in ("fact", "explain", "define"):
            return None, None
        prof = self.profile
        planners = [p for p in self.planners
                    if not isinstance(p, LMPlanner) or (prof.lm_planner and p.llm.available())]
        for p in planners:
            if p.label in ("learned", "neural"):    # the controllers: how many actions and plans they may try
                p.max_actions, p.max_plans = prof.ctl_actions, prof.ctl_plans
        words = None
        if self.proposer and prof.lm_planner and self.proposer.llm.available():
            # The model proposes plan + words; the executor checks both (computer/proposer.py).
            plan, res, words = propose_and_verify(text, self.proposer, self.navigator, planners, trace=trace)
        else:
            plan, res = navigate(text, planners, self.navigator, trace=trace, limit=prof.nav_limit)
        if plan is not None and str(plan.source).startswith("neural:") and not (res and res.answered):
            # The neural core only ever adds answers that ran, answered and passed accept().  Its other outcomes (a
            # plan that found nothing, a conflict accept() never saw) are dropped, and the turn goes on exactly as it
            # would without the model: the other planners' navigation, run again without it (their plans come first,
            # so this repeats only plans that already ran and found no answer).
            trace.append(f"neural plan {res.status if res else 'without a result'}: not said; answered as without "
                         "the neural core")
            others = [p for p in planners if getattr(p, "label", "") != "neural"]
            plan, res = navigate(text, others, self.navigator, limit=prof.nav_limit) if others else (None, None)
            words = None
        self.last_navigation = (plan, res)
        if not res or res.status in ("no_entity", "invalid"):
            return None, None
        self.context.remember(plan, res)
        if res.subjects:
            self.corpus.last_subject = res.subjects[0]
        mood = "happy" if res.answered and (res.confidence or 1) >= 0.7 else "thinking"
        reply = Reply(words or res.text, mood, "knowledge", voice=self.proposer.label if words else "computer")
        if res.rules:
            trace.append(f"knowledge rules: {', '.join(res.rules)} ({res.truth or res.status})")
        if (words or mode == "always" or plan.kind in ADVANCED or plan.kind in ("lm", "learned")
                or res.status == "conflict"):
            return reply, None
        return None, (reply if res.answered else None)

    def _integrity_refusal(self, r: Reply, route: Route) -> Reply:
        """corpus_integrity = "strict": an answer from a corpus that failed its check isn't given."""
        said = ("INTEGRITY FAILURE: the corpus does not match its build record. Answers are withheld until it is "
                "rebuilt or restored.")
        out = Reply(said, "concerned", r.faculty, route=route, trace=r.trace + [f"integrity: {self.corpus.integrity}, "
                                                                               "answer withheld (strict)"])
        env = dict(r.envelope)
        env.update(status="unavailable", truth=None, value=None, certainty="UNKNOWN", text=said, evidence=[],
                   errors=[e for e in env.get("errors", []) if e.get("code") == "CORPUS_INTEGRITY"])
        out.envelope = envelope_mod.seal(env)
        return out

    def _envelope(self, r: Reply, route: Route, analysed) -> dict:
        """The result envelope for this turn (computer/envelope.py), from whatever produced the reply."""
        reg, use = self.registry, self.registry.turn
        year = current_year()
        try:
            if analysed is not None:
                for c in ("analysis.estimate", "logic.formula", "logic.units"):
                    reg.mark(c)
                return envelope_mod.from_answer(analysed, self.corpus, use, self.analyst.year or year)
            if r.faculty == "knowledge":
                if self._said_by == "navigator" and self.last_navigation and self.last_navigation[1] is not None:
                    plan, res = self.last_navigation
                    # A learned plan's source is "<planner>:<action> <score>" ("learned:hop 0.97"): the planner is
                    # the part before the colon.  (Matching the whole source credited the controller's answers to
                    # planner.lm, a capability that wasn't used.)
                    src = (plan.source or "") if plan is not None else "rules"
                    src = src.split(":", 1)[0] if re.match(r"^(learned|neural):", src) else src
                    reg.mark({"rules": "planner.rules", "learned": "planner.controller",
                              "neural": "planner.neural"}.get(src, "planner.lm"))
                    if res.mapped:
                        reg.mark("knowledge.lexicon")
                    if any(not r.startswith("unit.") for r in res.rules):
                        reg.mark("knowledge.rules")
                    return envelope_mod.from_result(res, self.corpus, use, self.knowledge, text=r.text)
                if self._said_by == "lookup" and self._last_lookup:
                    reg.mark("corpus.lookup")
                    b = self._last_lookup
                    facts = b.get("facts", [])[: b.get("nwanted") or len(b.get("facts", []))]
                    return envelope_mod.simple("knowledge", r.text, self.corpus, use, year, "ESTABLISHED", evidence=facts)
                reg.mark("corpus.lookup")
                if self._said_by == "unreachable":
                    err = reg.error("corpus.lookup", "MISSING_FACT", r.text, True, {"why": "no corpus loaded"})
                    return envelope_mod.simple("knowledge", r.text, self.corpus, use, year, status="unavailable",
                                               errors=[err])
                err = reg.error("corpus.lookup", "MISSING_FACT", r.text, True, {"question": route.intent})
                return envelope_mod.simple("knowledge", r.text, self.corpus, use, year, status="unsupported", errors=[err])
            if route.intent == "calc":
                reg.mark("logic.calc")
                return envelope_mod.simple("calc", r.text, self.corpus, use, year, "ESTABLISHED",
                                           value=route.calc["value"] if route.calc else None)
            if route.intent == "clock":
                reg.mark("system.clock")
                return envelope_mod.simple("clock", r.text, self.corpus, use, year, "ESTABLISHED")
            if route.intent == "status":
                reg.mark("system.diagnostics")
                return envelope_mod.simple("status", r.text, self.corpus, use, year, "ESTABLISHED")
            if r.faculty == "instinct":
                reg.mark("instinct.iching")
            return envelope_mod.simple(r.faculty or "logic", r.text, self.corpus, use, year)   # no claim to support
        except Exception as e:  # noqa: BLE001 - the envelope must never stop an answer
            return {"status": "invalid", "error": f"envelope not built: {e}"}

    def _knowledge(self, text: str, route: Route, trace: list) -> Reply:
        """The knowledge pathway: which step comes next is decided by the rules in computer/data/rules.json
        (navigate, look up, say which answer, add the expert's line, admit it).  Each step is code; the
        order and the fallbacks are data, and -v shows every rule that fired."""
        state = {"stage": "start", "intent": route.intent, "corpus_ready": bool(self.corpus.ready)}
        got: dict = {}

        def navigate(st):
            now, backup = self._navigate(text, route, trace)
            got["now"], got["backup"] = now, backup
            res = self.last_navigation[1] if self.last_navigation else None
            st.update(stage="navigated", nav_status=res.status if res else "none",
                      nav_now=now is not None, nav_backup=backup is not None)

        def lookup(st):
            got["spoken"], got["conf"] = self._lookup(text, route, trace)
            res = self.last_navigation[1] if self.last_navigation else None
            b = self._last_lookup or {}
            if (got["spoken"] and res is not None and res.status == "unsupported" and res.subjects
                    and b.get("subject") not in res.subjects and self._names_a_relation(b.get("subject", ""))):
                # The navigator found the question's subject (Wolf) and that it lacks the relation; the lookup's only
                # answer is about the relation's own name (LANGUAGE).  Say what the navigator found instead.
                trace.append(f"lookup answered about {b.get('subject')}, the relation's name, not {res.subjects[0]}: "
                             "the navigator's finding is said instead")
                got["spoken"] = ""
                got["backup"] = Reply(res.text, "thinking", "knowledge")
                st["nav_backup"] = True
            st.update(stage="looked_up", lookup_spoken=bool(got["spoken"]))

        def say_navigator(st, which="now"):
            self._said_by = "navigator"
            return got[which] or got["now"] or got["backup"]

        def say_lookup(st, tail=""):
            self._said_by = "lookup"
            spoken = got.get("spoken", "")
            if tail == "health":
                return Reply(f"{spoken} {HEALTH_TAIL}".strip(), "concerned", "knowledge")
            if tail == "money":
                return Reply(f"{spoken} {MONEY_TAIL}".strip(), "thinking", "knowledge")
            return Reply(spoken, "happy" if (got.get("conf") or 1) >= 0.7 else "thinking", "knowledge")

        def say_unreachable(st):
            self._said_by = "unreachable"
            return Reply("ERROR: corpus unavailable. No answer generated; facts are not guessed.", "concerned", "knowledge")

        def say_unknown(st):
            self._said_by = "unknown"
            return Reply("NOT FOUND: no matching fact in the corpus. No answer generated; facts are not guessed.",
                         "thinking", "knowledge")

        handlers = {"navigate": navigate, "lookup": lookup, "say_navigator": say_navigator, "say_lookup": say_lookup,
                    "say_unreachable": say_unreachable, "say_unknown": say_unknown}
        self.last_navigation = None
        reply, _ = run_pathway(self.pathways["knowledge"], state, handlers, trace)
        return reply if reply is not None else say_unknown(state)

    def _names_a_relation(self, key: str) -> bool:
        words = [w for w in key.lower().split("_") if w]
        vocab = self.corpus.rel_vocab()
        return bool(words) and all(w in vocab or w + "s" in vocab for w in words)

    def _lookup(self, text: str, route: Route, trace: list) -> tuple[str, float | None]:
        """The one-shot lookup: what it would say ("" if nothing that answers the question), and its confidence."""
        ans = self.corpus.ask(text) if self.corpus.ready else {"found": False, "blocks": []}
        trace.append("corpus: " + (", ".join(b["subject"] + ("*" if b["matched"] else "") for b in ans["blocks"])
                                   if ans["found"] else "nothing found"))
        spoken = ""
        if ans["found"]:
            b = ans["blocks"][0]
            self._last_lookup = b
            if b["matched"]:
                spoken = phrase_block(b)
            else:
                # Nothing matched what was asked.  For a bare name ("owls", "tell me about mars")
                # say its basics; for a specific question, admit it rather than say something beside the point.
                content = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP]
                subj = set(b["subject"].lower().split("_"))
                if all(w in subj or w.rstrip("s") in subj for w in content):
                    self._last_lookup = {**b, "nwanted": min(3, len(b.get("facts", [])))}
                    spoken = phrase_block(self._last_lookup)
        if route.sensitive and "wisdom" in route.also:
            # "Should I stop my medicine?" is a decision for a person, not a dictionary or the sampler.
            spoken = ""
        return spoken, ans.get("confidence")

    def _instinct(self, text: str, route: Route, shake: bytes, trace: list) -> Reply:
        if route.intent == "saying":
            line, n = instinct.wise_saying()
            trace.append(f"saying from hexagram {n}")
            return Reply(line, "wink", "instinct")

        if route.intent == "more":
            if not self.last:
                return Reply("NO PRIOR SAMPLE: the sampler has not run this session.", "curious", "instinct")
            p = self.last
            extra = ""
            theme = p.reading.primary.info["theme"]
            d = self._definition(theme, p)
            if d:
                extra = f" CORPUS: {theme}: {d}."    # Knowledge joins in
            return Reply(p.more + extra, p.mood, "instinct", perspective=p)

        method = self.cfg.method
        p = instinct.perspective(text, method=method, extra=shake)
        self.last = p
        trace.append(f"cast ({method}, {p.reading.entropy}): {p.reading.lines} -> {p.reading.primary.n} "
                     f"{p.reading.primary.name}" + (f" -> {p.reading.relating.n} {p.reading.relating.name}"
                                                    if p.reading.relating else ""))
        aside = self._aside(text, trace) if self.cfg.knowledge_aside else ""
        notes = (f"{aside} " if aside else "") + p.brief
        reply = Reply(notes, p.mood, "instinct", perspective=p)

        if not self.profile.lm_wording:            # don't even ask whether the server is up
            if self.llm and self.cfg.llm_mode != "off":
                trace.append(f"mini LM resting ({self.profile.name}): template output")
            return reply
        lm = self._lm()
        if lm:
            worded = self._word(text, notes, p, trace, lm)
            if worded:
                reply.text, reply.voice = worded, lm.label
        return reply

    def _lm(self) -> MiniLM | None:
        """The model for this turn: priority may use a bigger one, if it's up."""
        if self.cfg.llm_mode == "off":
            return None
        if self.profile.name == "priority" and self.llm_priority and self.llm_priority.available():
            return self.llm_priority
        return self.llm if self.llm and self.llm.available() else None

    # ------------------------------------------------------------------ helpers

    def _aside(self, text: str, trace: list) -> str:
        """Knowledge speaks first: one known fact about a named thing in the question."""
        if not self.corpus.ready or (self.cfg.corpus_integrity == "strict" and not self.corpus.integrity_ok):
            return ""
        ans = self.corpus.ask(text)
        for b in ans.get("blocks", []):
            if b.get("reverse"):
                continue
            # Only named things (Jupiter, Paris), not everyday words (garden, life, job):
            # mostly curated facts and no dictionary entry.
            allf = self.corpus.facts_for(b["subject"])
            curated = [f for f in allf if f.source != "OEWN"]
            if len(curated) < 10 or len(curated) < 0.8 * len(allf):
                continue
            is_a = sorted((f for f in curated if f.relation == "IS_A"), key=lambda f: SOURCE_RANK.get(f.source, 3))
            if is_a:
                s = phrase_block({"subject": b["subject"], "facts": is_a[:1], "nwanted": 1})
                trace.append(f"knowledge aside: {s}")
                return f"KNOWN FACT: {s}"
        return ""

    def _definition(self, word: str, p: instinct.Perspective) -> str:
        """The dictionary sense of the theme word that best fits the hexagram's own words."""
        facts = [f for f in self.corpus.facts_for(word.upper()) if f.relation == "DEFINITION"] if self.corpus.ready else []
        if not facts:
            return ""
        order, _ = rank_by_overlap([f.value for f in facts], p.more + " " + p.brief)   # same ranking as RANK
        return re.sub(r';\s*"[^"]*"', "", facts[order[0]].value).strip().rstrip(".")

    def _check(self, said: str, notes: str, question: str, p: instinct.Perspective) -> str:
        """Why an LM reply can't be used, or '' if it can."""
        if not said:
            return "empty"
        if len(said.split()) > self.cfg.llm_max_words * 1.3:
            return "too long"
        if p.reading.primary.name.lower().removeprefix("the ") not in said.lower():
            return "dropped the hexagram's name"
        bad = ungrounded_numbers(said, notes, question)
        if bad:
            return f"added numbers {bad}"
        if PREDICTS.search(said):
            return "predicted the future"
        return ""

    @staticmethod
    def _clean(raw: str) -> str:
        s = re.sub(r"[*#`_]+", "", raw or "")
        s = re.sub(r"^\s*\w+\s*:\s*", "", s) if re.match(r"^\s*(computer|cdos|assistant|owl|ollie)\s*:", s, re.I) else s
        return " ".join(s.split())

    def _word(self, question: str, notes: str, p: instinct.Perspective, trace: list, lm: MiniLM | None = None) -> str:
        persona = PERSONA.format(name=self.cfg.name, user=self.cfg.user)
        lm = lm or self.llm
        max_tokens = int(self.cfg.llm_max_words * self.profile.lm_tokens)
        try:
            if self.cfg.llm_mode == "tools":
                said = self._tool_turn(question, p, trace, lm, max_tokens)
            else:
                msgs = [{"role": "system", "content": persona},
                        {"role": "user", "content": f"{self.cfg.user} asked: {question}\n\nNOTES:\n{notes}\n\n"
                                                    f"Card: {json.dumps(p.card)}\n\nNow output the result."}]
                said = self._clean(lm.chat(msgs, max_tokens=max_tokens))
                self._lm_calls.append(dict(getattr(lm, "last_timings", {}), model=lm.label))
        except Exception as e:  # noqa: BLE001 - any model failure falls back to template output
            trace.append(f"mini LM failed: {e}")
            return ""
        why = self._check(said, notes, question, p)
        trace.append(f"mini LM: {said!r}" + (f" -> discarded ({why})" if why else ""))
        return "" if why else said

    def _tool_turn(self, question: str, p: instinct.Perspective, trace: list, lm: MiniLM | None = None,
                   max_tokens: int = 0) -> str:
        """Let the model call the tools itself.  consult_iching returns the cast already made."""
        msgs = [{"role": "system", "content": TOOL_PERSONA.format(name=self.cfg.name, user=self.cfg.user)},
                {"role": "user", "content": question}]
        for _ in range(3):
            lm = lm or self.llm
            m = lm.complete(msgs, tools=TOOLS, max_tokens=max_tokens or int(self.cfg.llm_max_words * 1.8))
            self._lm_calls.append(dict(getattr(lm, "last_timings", {}), model=lm.label))
            calls = m.get("tool_calls") or []
            if not calls:
                return self._clean(m.get("content") or "")
            msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
            for c in calls:
                name = c.get("function", {}).get("name")
                trace.append(f"tool call: {name}")
                if name == "consult_iching":
                    result = {**p.card, "template_output": p.brief}
                elif name == "lookup_fact":
                    args = json.loads(c["function"].get("arguments") or "{}")
                    result = {"facts": as_context(self.corpus.ask(args.get("question", question)))}
                else:
                    result = {"error": "no such tool"}
                msgs.append({"role": "tool", "tool_call_id": c.get("id", name), "content": json.dumps(result)})
        return ""

    def _log(self, text: str, r: Reply) -> None:
        if not self.cfg.log_file:
            return
        entry = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "q": text, "intent": r.route.intent if r.route else "",
                 "faculty": r.faculty, "voice": r.voice, "a": r.text, "ms": r.ms, "compute": r.compute,
                 # what the envelope says of the answer: the Growth Engine finds unanswered questions by it
                 "status": (r.envelope or {}).get("status"), "certainty": (r.envelope or {}).get("certainty")}
        if r.evidence:
            entry["evidence"] = r.evidence
        if r.perspective and r.route and r.route.intent != "more":
            entry["cast"] = r.perspective.card
        try:
            with open(self.cfg.log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass
