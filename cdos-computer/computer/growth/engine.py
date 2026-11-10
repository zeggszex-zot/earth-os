"""The Growth Engine's work: discovery, tasks, answers, runs.  See computer/growth/__init__.py for the cycle.

    <neural_registry>/growth/
        tasks/<task id>.json    one task: its findings, answers, candidate model and history (replaced atomically)
        tasks/<task id>/        the task's training examples (mine.jsonl), made from its answers
        journal.jsonl           append-only, hash-chained: every discovery, task, answer and run
        answers.jsonl           append-only, hash-chained: every answer a person gave, with their name

Kinds of finding (Finding.category):

    selector      the question names things the corpus has, and some plan shape reaches an answer that passes
                  accept(): the reasoning selector could learn it, once a person says which answer is right
    missing_relation  it names a thing the corpus has, but none of that thing's relations matches the other words
    corpus_gap    no name in it is in the corpus, or no plan shape reaches an accepted answer: facts are missing
    conflict      the corpus disagrees with itself (said as such): a curation job, not a learning one
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from computer.neural.experience import ChainLog, read_chain
from computer.neural.records import norm_q

TASK_FORMAT = "cdos-growth-task/1"
APPROVAL_POLICIES = ("manual",)
TASK_ID = re.compile(r"^gt-\d{8}t\d{6}-[0-9a-f]{6}$")
OPEN = ("needs_answers", "ready")


class GrowthError(RuntimeError):
    pass


@dataclass
class Finding:
    question: str
    status: str                                  # unsupported | conflict
    count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    sources: list = field(default_factory=list)  # [{"log": "experience" | "turns", "seq"/"line": n, "hash": ...}]
    said: str = ""                               # what the computer said
    category: str = ""                           # selector | missing_relation | corpus_gap | conflict
    why: str = ""
    names: list = field(default_factory=list)    # corpus keys the question names
    candidates: list = field(default_factory=list)   # [{"action", "answer"}]: accepted answers some plan shape reaches


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Growth:
    def __init__(self, registry, corpus_dir=None, turn_log=None, lexicon=None, approval: str = "manual"):
        if approval not in APPROVAL_POLICIES:
            raise GrowthError(f"growth_approval {approval!r}: the only policy there is, is {', '.join(APPROVAL_POLICIES)}")
        self.registry = Path(registry)
        self.root = self.registry / "growth"
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        self.corpus_dir = Path(corpus_dir) if corpus_dir else None
        self.turn_log = Path(turn_log) if turn_log else None
        self.lexicon = lexicon
        self.approval = approval
        self.journal = ChainLog(self.root / "journal.jsonl", fmt="cdos-growth-journal/1")
        self.answers = ChainLog(self.root / "answers.jsonl", fmt="cdos-growth-answer/1")
        self._computer = None

    # ------------------------------------------------------------------ 1. discover

    def _read_failures(self) -> "OrderedDict[str, Finding]":
        found: OrderedDict[str, Finding] = OrderedDict()
        per_log: dict = {}            # (question, log) -> count: one turn is in both logs, so a count is the larger

        def add(q, status, t, src, said):
            if not q or not q.strip():
                return
            k = norm_q(q)
            per_log[(k, src["log"])] = per_log.get((k, src["log"]), 0) + 1
            f = found.get(k)
            if f is None:
                found[k] = f = Finding(q, status, 1, t, t, [src], said)
            else:
                f.last_seen = t or f.last_seen
                if len(f.sources) < 10:
                    f.sources.append(src)
                if status == "conflict":
                    f.status = "conflict"
                f.said = f.said or said
            f.count = max(per_log.get((k, "experience"), 0), per_log.get((k, "turns"), 0))
        xp = self.registry / "experience" / "experience.jsonl"
        for rec in read_chain(xp):
            out = rec.get("outcome") or {}
            if out.get("status") in ("unsupported", "conflict"):
                add(rec.get("question", ""), out["status"], rec.get("t", ""),
                    {"log": "experience", "seq": rec.get("seq"), "hash": rec.get("hash")}, "")
        if self.turn_log and self.turn_log.exists():
            with open(self.turn_log, "r", encoding="utf-8") as fh:
                for n, raw in enumerate(fh, 1):
                    try:
                        e = json.loads(raw)
                    except ValueError:
                        continue
                    if e.get("faculty") != "knowledge":
                        continue
                    status = e.get("status")
                    if status is None and str(e.get("a", "")).startswith("NOT FOUND"):
                        status = "unsupported"            # a turn logged before 0.2 recorded the status
                    if status in ("unsupported", "conflict"):
                        add(e.get("q", ""), status, e.get("t", ""),
                            {"log": "turns", "line": n, "sha256": hashlib.sha256(raw.encode()).hexdigest()[:16]},
                            e.get("a", ""))
        return found

    def computer(self):
        """The computer as configured, for re-asking: no logs written, nothing remembered between questions."""
        if self._computer is None:
            from computer.config import Config
            from computer.core import Computer
            self._computer = Computer(Config(corpus=str(self.corpus_dir), llm_mode="off", llm_endpoint="", log_file="",
                                             state_file="", relation_lexicon=self.lexicon or "",
                                             neural_registry=str(self.registry), neural_experience=False))
        return self._computer

    def _classify(self, f: Finding) -> bool:
        """Re-ask it and look at it with the corpus.  False if it's answered now (fixed since it was logged)."""
        from computer.clock import current_year
        from computer.controller import ACTION_NAMES, ControllerPlanner, Uniform, fills
        from computer.corpus import key_of, rel_words
        from computer.lexicon import words_for
        from computer.plan import parse
        c = self.computer()
        c.context.clear()
        c.corpus.last_subject, c.last_navigation = "", None
        r = c.think(f.question)
        if r.envelope.get("status") == "answered":
            return False
        f.said = f.said or r.text
        if r.envelope.get("status") == "conflict" or f.status == "conflict":
            f.category, f.why = "conflict", "the corpus disagrees with itself here: curate the facts"
            return True
        ctl = ControllerPlanner(c.corpus, Uniform())
        rd = ctl.reader.read(f.question)
        names = [s for s in rd.spans if not s.relish]
        f.names = [key_of(s.phrase.replace(" ", "_")) for s in names]
        seen = set()
        free = [rd.toks[i] for i in rd.free] + [s.phrase for s in rd.spans if s.relish]
        for a in ACTION_NAMES:
            for text in fills(a, rd):
                p = parse(text)
                res = ctl.nav.run(p)
                if res.answered and not ctl.accept(f.question, p, res) and res.text not in seen:
                    seen.add(res.text)
                    # how many of the question's other words the facts used are named by: accept() doesn't require
                    # it ("what does a whale eat" passes with "Whale is a marine mammal."), so the person sees the
                    # better-covering answers first
                    rw = set().union(*(words_for(c.corpus, x.relation) for x in res.evidence)) if res.evidence else set()
                    covers = sum(1 for w in free if w in rw or w.rstrip("s") in rw or any(w in h for h in rw if len(w) > 3))
                    f.candidates.append({"action": a, "answer": res.text[:300], "covers": covers,
                                         "of_words": len(free)})
        f.candidates.sort(key=lambda x: -x["covers"])
        f.candidates = f.candidates[:6]
        if f.candidates:
            f.category, f.why = "selector", ("a plan shape reaches an answer that passes the checks; a person can say "
                                             "which (if any) is right, and the selector can learn it")
            return True
        if not names:
            f.category, f.why = "corpus_gap", "nothing the question names is in the corpus"
            return True
        free = [rd.toks[i] for i in rd.free]
        thing = names[0]
        rels = {x.relation for x in ctl.nav.facts(key_of(thing.phrase.replace(" ", "_")), current_year())}
        wanted = [w for w in free if not any(w in words_for(c.corpus, r) or w.rstrip("s") in rel_words(r) for r in rels)]
        if free and wanted:
            f.category = "missing_relation"
            f.why = (f"{thing.phrase} is in the corpus, but none of its relations ({', '.join(sorted(rels)[:8]) or 'none'}) "
                     f"matches {', '.join(wanted)}: a relation (or a lexicon word for one) is missing")
        else:
            f.category, f.why = "corpus_gap", "no plan shape reaches an answer the checks accept: the fact may be missing"
        return True

    def discover(self, replay: bool = True) -> list[Finding]:
        """Failures from the logs, each re-asked (with a corpus) so only what still fails is reported."""
        found = list(self._read_failures().values())
        if replay and self.corpus_dir:
            found = [f for f in found if self._classify(f)]
        else:
            for f in found:
                f.category = f.category or ("conflict" if f.status == "conflict" else "unclassified")
        self.journal.append({"event": "discover", "findings": len(found),
                             "by_category": {k: sum(f.category == k for f in found)
                                             for k in sorted({f.category for f in found})}})
        return found

    # ------------------------------------------------------------------ 2. tasks

    def _path(self, task_id: str) -> Path:
        if not TASK_ID.match(task_id or ""):
            raise GrowthError(f"{task_id!r} isn't a task id")
        return self.root / "tasks" / f"{task_id}.json"

    def tasks(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "tasks").glob("gt-*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def task(self, task_id: str) -> dict:
        p = self._path(task_id)
        if not p.exists():
            raise GrowthError(f"no task {task_id}")
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, task: dict, event: str, **details) -> dict:
        from computer.neural.registry import write_atomic
        task["history"].append({"t": now(), "event": event, **details})
        write_atomic(self._path(task["id"]), (json.dumps(task, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        self.journal.append({"event": event, "task": task["id"], **details})
        return task

    def create_tasks(self, findings: list[Finding]) -> list[dict]:
        """One task per kind of work.  A question already in an open task isn't added again."""
        open_qs = {norm_q(q["question"]) for t in self.tasks() if t["status"] in OPEN + ("report",)
                   for q in t["questions"]}
        made = []
        groups = (("retrain_selector", ("selector",), "needs_answers",
                   "the computer couldn't answer these, but the corpus can: say which answer is right, then run the task"),
                  ("corpus_gap", ("corpus_gap", "missing_relation", "conflict", "unclassified"), "report",
                   "the corpus lacks (or contradicts itself on) what these need: for whoever builds the corpus; "
                   "the Growth Engine never writes facts"))
        for kind, cats, status, reason in groups:
            qs = [f for f in findings if f.category in cats and norm_q(f.question) not in open_qs]
            if not qs:
                continue
            body = json.dumps([f.question for f in qs], sort_keys=True)
            tid = f"gt-{time.strftime('%Y%m%dt%H%M%S', time.gmtime())}-{hashlib.sha256((kind + body).encode()).hexdigest()[:6]}"
            task = {"format": TASK_FORMAT, "id": tid, "kind": kind, "component": "reasoning.selector",
                    "created": now(), "status": status, "reason": reason, "approval": self.approval,
                    "questions": [asdict(f) for f in qs], "answers": [], "candidate": None, "history": []}
            made.append(self._save(task, "task_created", kind=kind, questions=len(qs)))
        return made

    # ------------------------------------------------------------------ answers from a person

    def answer(self, task_id: str, question: str, gold: list[str], by: str, expect: str = "answered",
               kind: str = "hop", check: bool = True, authorized: dict | None = None) -> dict:
        """A person says what the right answer is.  Recorded in answers.jsonl (append-only) with their name, and checked
        against the corpus: which plan shapes reach it (none = the selector can't learn it from this corpus)."""
        task = self.task(task_id)
        if task["kind"] != "retrain_selector" or task["status"] not in OPEN:
            raise GrowthError(f"{task_id} is {task['kind']} / {task['status']}: it doesn't take answers")
        q = next((x for x in task["questions"] if norm_q(x["question"]) == norm_q(question)), None)
        if q is None:
            raise GrowthError(f"{question!r} isn't one of {task_id}'s questions")
        if not by or not by.strip():
            raise GrowthError("say who is answering (--by): an answer without a person isn't evidence")
        if expect not in ("answered", "unsupported"):
            raise GrowthError("expect is answered or unsupported")
        gold = [g.strip() for g in gold if g and g.strip()]
        if expect == "answered" and not gold:
            raise GrowthError("an answered question needs the answer (--gold)")
        reach = None
        if check and self.corpus_dir:
            from computer.controller import ControllerPlanner, Uniform
            from computer.neural.records import explore_detailed
            ctl = ControllerPlanner(self.computer().corpus, Uniform())
            reach, _, _ = explore_detailed({"question": q["question"], "gold": gold, "expect": expect, "kind": kind}, ctl)
        rec = {"task": task_id, "question": q["question"], "gold": gold, "expect": expect, "kind": kind, "by": by.strip(),
               "reachable_by": reach}
        if authorized:                          # the Governor's decision: who really ran the command, and its record
            rec["authorized"] = authorized
        self.answers.append(rec)
        task["answers"] = [a for a in task["answers"] if norm_q(a["question"]) != norm_q(q["question"])]
        task["answers"].append({**rec, "t": now(), "answer_seq": self.answers.seq, "answer_hash": self.answers.prev})
        if any(a["expect"] == "answered" and a.get("reachable_by") != [] for a in task["answers"]):
            task["status"] = "ready"           # at least one answer the selector can learn from
        why = ("" if reach is None or reach or expect != "answered" else
               "no plan shape reaches this answer with this corpus: the selector can't learn it (a corpus gap?)")
        return self._save(task, "answer", question=q["question"], by=by.strip(), reachable_by=reach, note=why)

    # ------------------------------------------------------------------ 3-4. experiment and evaluate

    def run(self, task_id: str, heldout=(), regression=(), log=None, **train_kw) -> dict:
        """Train a candidate with the task's answers into staging, evaluate it, record the result.  Never promotes."""
        from computer.neural.evaluate import evaluate
        from computer.neural.train import TrainingError, train_to_staging
        task = self.task(task_id)
        if task["status"] != "ready":
            raise GrowthError(f"{task_id} is {task['status']}: it runs once a person has answered at least one question")
        if not self.corpus_dir:
            raise GrowthError("running a task needs the corpus")
        work = self.root / "tasks" / task_id
        work.mkdir(exist_ok=True)
        mine = work / "mine.jsonl"
        mine.write_text("".join(json.dumps({"question": a["question"], "gold": a["gold"], "expect": a["expect"],
                                            "kind": a["kind"], "by": a["by"], "task": task_id}, sort_keys=True) + "\n"
                                for a in task["answers"]), encoding="utf-8")
        self._save(task, "run_started", answers=len(task["answers"]))
        report: dict = {}
        try:
            d = train_to_staging(self.registry, self.corpus_dir, mine=[mine], heldout=heldout, regression=regression,
                                 lexicon=self.lexicon, log=log, report=report, require_mine=True, **train_kw)
        except TrainingError as e:
            msg = str(e)
            if "already exists" in msg:
                msg = ("the answers didn't change what the network learns (the same weights are already staged): "
                       "more answers, or answers some plan shape can reach, are needed")
            task["status"] = "needs_answers"
            task["candidate"] = {"error": msg, "used": report.get("mine_used", []),
                                 "not_used": report.get("mine_dropped", [])}
            return self._save(task, "run_refused", why=msg, used=len(report.get("mine_used", [])))
        rep = evaluate(self.registry, d.name, self.corpus_dir, heldout, regression, self.lexicon, log=log)
        own = self._own_questions(d, task, {u["question"] for u in report.get("mine_used", [])})
        task["candidate"] = {"model_id": d.name, "passed": rep["passed"], "primary": rep["primary"],
                             "used": report.get("mine_used", []), "not_used": report.get("mine_dropped", []),
                             "selection": rep["selection"],
                             "failed_gates": {g: v["why"] for g, v in rep["gates"].items() if not v["pass"]},
                             "task_questions": own,
                             "promote": f"python3 -m computer.neural promote --registry {self.registry} --model {d.name}"
                                        " --approve"}
        task["status"] = "evaluated" if rep["passed"] else "rejected"
        return self._save(task, "evaluated" if rep["passed"] else "rejected", model_id=d.name, passed=rep["passed"],
                          task_questions=own["right"])

    def _own_questions(self, model_dir: Path, task: dict, used: set = frozenset()) -> dict:
        """The task's own questions, asked of the whole computer with the candidate.  The ones trained on show only that
        the candidate learned them (the held-out evaluation is for generalising); the others (dropped, see "not_used")
        it answers without having seen them."""
        from computer.corpus import Corpus
        from computer.navdata import score
        from computer.neural.evaluate import computers_for
        from computer.neural.model import load_model_dir
        corpus = Corpus(self.corpus_dir).load()
        net, man = load_model_dir(model_dir)
        un, _ = load_model_dir(model_dir, "untrained")
        comp = computers_for(corpus, net, man, un)["computer+trained"]
        out = {"right": 0, "wrong": 0, "abstained": 0, "detail": []}
        for a in task["answers"]:
            comp.context.clear()
            comp.corpus.last_subject = ""
            r = comp.think(a["question"])
            s = score({"kind": a["kind"], "gold": a["gold"], "expect": a["expect"]}, r.text,
                      r.envelope.get("status") == "answered")
            out[s] += 1
            out["detail"].append({"question": a["question"], "score": s, "said": r.text[:200],
                                  "trained_on": a["question"] in used})
        return out

    # ------------------------------------------------------------------ unattended

    def auto(self, heldout=(), regression=(), log=None, **train_kw) -> dict:
        """What the timer runs: discover, make tasks, run the tasks people have answered.  Promotes nothing."""
        found = self.discover()
        made = self.create_tasks(found)
        ran = []
        for t in self.tasks():
            if t["status"] == "ready":
                ran.append(self.run(t["id"], heldout, regression, log=log, **train_kw)["id"])
        return {"findings": len(found), "tasks_created": [t["id"] for t in made], "tasks_run": ran,
                "approval": self.approval}
