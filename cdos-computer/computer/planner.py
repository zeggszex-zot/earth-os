"""Planners: question -> plan (computer/plan.py).

RulePlanner   deterministic patterns.  Runs anywhere, explains itself, and is the *teacher*:
              tools/make_navigator_data.py uses plans like these to make training data.
LMPlanner     a small model writes the plan (llama-server, grammar-constrained so it can only
              emit the plan language).  Its plan is validated before it runs; if it is invalid,
              or leads nowhere, the rules get their turn.

`navigate()` is the loop: plan -> validate -> run -> (another planner if that failed) -> result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import STOP, Corpus
from .navigator import DIMENSIONS, Navigator, rel_phrase
from .phrase import name_of
from .plan import GRAMMAR, Plan, PlanError, parse

# Dictionary questions, as plan relations.  The same words are used by the rule planner, the
# training-data generator (computer/navdata.py) and the few-shot prompts, so a model learns one form.
LEX = {"define": "~definition", "synonym": "~synonym,similar", "antonym": "~antonym", "hypernym": "~isa,hypernym",
       "hyponym": "~hyponym", "derivation": "~derivation", "example": "~example"}
LEX_KINDS = set(LEX) | {"lex_chain", "lex_test", "lex_count"}
FOLLOWUP_KINDS = {"more", "about", "switch", "that"}

# Kinds of question the old one-shot lookup can't do: the navigator owns these in "auto" mode.
ADVANCED = {"chain", "compare", "test", "logic", "reverse", "explain", "count", "calc", "require"} | LEX_KINDS | FOLLOWUP_KINDS
STOP_KINDS = {"calc", "require"}     # when one of these can't be done, that's the answer: no part-answer from another reading

GENERIC = {"things", "thing", "ones", "one", "stuff", "places", "kinds", "types"}
# "more massive" for CALC: the measurement an adjective means, where its own word names no relation.
CALC_ADJ = {"massive": "heavy", "populous": "populous", "distant": "far", "voluminous": "big"}
WH = {"what", "which", "who", "whom", "where", "when", "how", "why"}
AUX = {"is", "are", "was", "were", "does", "do", "did", "has", "have", "can", "will", "the", "a", "an", "'s", "s"}
PARTICIPLE = {"located", "found", "made", "part", "situated", "named", "called", "based", "built", "founded",
              "orbited", "bordered", "spoken", "used", "eaten", "owned", "ruled", "governed"}
LESS_WORDS = {"smaller", "lighter", "shorter", "older", "colder", "closer", "nearer", "slower", "lower",
              "fewer", "less", "shallower", "narrower"}
COMPARATIVE = {"bigger": "big", "larger": "large", "smaller": "small", "heavier": "heavy", "lighter": "light",
               "taller": "tall", "shorter": "short", "longer": "long", "older": "old", "newer": "new",
               "younger": "young", "hotter": "hot", "colder": "cold", "farther": "far", "further": "far",
               "closer": "close", "nearer": "near", "faster": "fast", "slower": "slow", "higher": "high",
               "lower": "high", "deeper": "deep", "wider": "wide", "greater": "", "more": "", "fewer": "",
               "less": ""}
# "computer" / "cdos" (and the legacy "owl" / "ollie") address the device only before a comma or a question word:
# "owl diet" is about owls, "computer memory" is about computers.
FILLER = re.compile(r"^(?:(?:hey|ok|okay|please|so|um)\b[ ,]*|(?:computer|cdos|owl|ollie)(?:\s*,\s*|\s+(?=(?:what|which|who|where|when|"
                    r"how|why|is|are|does|do|can|tell)\b)))+")
ASKING = re.compile(r"^(?:(?:can|could|would) you (?:please )?(?:tell me|say)\s+|do you know\s+|tell me\s+|"
                    r"i (?:want|would like) to know\s+)(?=(?:what|which|who|where|when|how|why|is|are|does|do)\b)")


YEAR = re.compile(r"\s*\b(?:in|during|around|as of)\s+(1[0-9]{3}|20[0-9]{2})\b")


def year_in(q: str) -> int:
    m = YEAR.search(q or "")
    return int(m.group(1)) if m else 0


def normalize(q: str) -> str:
    q = (q or "").lower().replace("\u2019", "'").strip()
    q = re.sub(r"[?!.]+$", "", q).strip()
    q = YEAR.sub("", q)
    q = FILLER.sub("", q)
    q = ASKING.sub("", q)
    q = re.sub(r"\s*,\s*", ", ", q)
    return " ".join(q.split())


def content(words_text: str) -> list[str]:
    ws = re.findall(r"[a-z]+", words_text.lower())
    return [w for w in ws if w not in STOP and w not in WH and w not in ("many", "much", "kind", "type", "sort", "name")]


def rel_of(words_text: str) -> str:
    ws = content(words_text)
    if len(ws) > 1 and " of " in words_text:           # "number of moons" -> one relation's words
        ws = [w for w in re.findall(r"[a-z]+", words_text) if w not in STOP and w not in WH]
    return "~" + ",".join(dict.fromkeys(ws)) if ws else ""


def verb_stem(v: str) -> str:
    """orbits -> orbit, breathes -> breathe, pushes -> push, crosses -> cross; others unchanged."""
    if re.search(r"(sh|ch|x|ss|z)es$", v):
        return v[:-2]
    if v.endswith("s") and not v.endswith("ss") and len(v) > 3:
        return v[:-1]
    return v


def strip_article(s: str) -> str:
    return re.sub(r"^(?:the|a|an|some|any)\s+", "", s.strip(" ,"))


# ---------------------------------------------------------------------- dictionary patterns

_SENSE = (r"(?:,? (?:as in|like in|like|in the sense of|when (?:talking|you talk|we talk|people talk|you're talking) about|"
          r"in the context of|to do with|that has to do with|about) (?P<ctx>.+))?")
_W = r"(?:the word )?(?P<x>.+?)"
LEX_PATTERNS = [   # (kind, regex), tried in order; x is the word, ctx (definitions only) picks a sense
    ("example", rf"^(?:use|put) {_W} in a sentence$"),
    ("example", rf"^(?:give me |show me |what is |tell me )?(?:an |some )?example sentences? (?:for|with|of|using) {_W}$"),
    ("example", rf"^(?:give me |show me )?an example of {_W} in a sentence$"),
    ("example", rf"^how (?:do|would|can|should) (?:you|i|we) use {_W}(?: in a sentence)?$"),
    ("define", rf"^what (?:does|do|did) {_W} (?:mean|means|stand for){_SENSE}$"),
    ("define", rf"^what is {_W} mean{_SENSE}$"),
    ("define", rf"^what (?:is|are) (?:the )?(?:meaning|meanings|definition|definitions|senses?) of {_W}{_SENSE}$"),
    ("define", rf"^(?:define|definition of|meaning of|explain the word|what is meant by|look up) {_W}{_SENSE}$"),
    ("define", rf"^(?:explain |say )?what {_W} means{_SENSE}$"),
    ("define", rf"^{_W} (?:meaning|definition|means what)$"),
    ("synonym", rf"^(?:what (?:is|are) |give me |tell me |list |say |find |know )?(?:a |an |some |the |any )?"
                rf"(?:other |another |different |more |good |better |fancy |fancier |simpler |nicer |smarter |cooler |"
                rf"similar )?(?:word|words|synonym|synonyms) (?:for|of|like|"
                rf"that means?|meaning|similar to|that mean)(?: the same(?: thing)? as)? {_W}$"),
    ("synonym", rf"^what (?:word|words) (?:means?|mean|is like|are like) (?:the same(?: thing)? as )?{_W}$"),
    ("synonym", rf"^(?:what )?(?:is|are) (?:there )?(?:another|other|any other) words? for {_W}$"),
    ("synonym", rf"^{_W} (?:synonym|synonyms)$"),
    ("antonym", rf"^(?:what (?:is|are) |give me |tell me |say )?(?:the |an |some |a )?(?:opposite|opposites|antonym|"
                rf"antonyms)(?: word| words)? (?:of|for|to) {_W}$"),
    ("antonym", rf"^what (?:word|words) (?:means?|mean|is|are) (?:the )?opposite (?:of|to) {_W}$"),
    ("antonym", rf"^{_W} (?:antonym|antonyms|opposite)$"),
    ("hypernym", rf"^what (?:kind|type|sort) of [a-z]+ (?:is|are|was) {_W}$"),
    ("hypernym", rf"^what (?:is|are) {_W} (?:a |an )?(?:kind|type|sort|form) of$"),
    ("hypernym", rf"^{_W} (?:is|are) (?:a |an )?(?:kind|type|sort) of what$"),
    ("hypernym", rf"^what (?:category|class|group) (?:does|do|is|are) {_W}(?: belong to| belong in| in| fall under| part of)?$"),
    ("hypernym", rf"^what is (?:the |a )?(?:hypernym|broader word|more general word) (?:of|for) {_W}$"),
    ("hyponym", rf"^(?:what (?:are|is) |what |name |list |give me |tell me |say )?(?:some |the |a few |any )?"
                rf"(?:kinds|types|sorts|examples|varieties|hyponyms) of {_W}(?: are there| do you know| is there)?$"),
    ("derivation", rf"^(?:what )?(?:words|word|other forms|forms)(?: are| is| come| comes)? (?:related to|from|derived from|"
                   rf"that come from) {_W}$"),
    ("derivation", rf"^what (?:is|are) (?:the )?(?:noun|verb|adjective|adverb|other|related) (?:form|forms|word|words) "
                   rf"(?:of|for) {_W}$"),
    ("derivation", rf"^(?:give me |name |list )?(?:some )?words related to {_W}$"),
]
LEX_TESTS = [      # (kind, regex): is x linked to y?
    ("synonym", r"^(?:is|are) (?P<x>.+?) (?:a |another )?(?:synonym|word) (?:of|for) (?P<y>.+)$"),
    ("synonym", r"^(?:does|do) (?P<x>.+?) mean (?:the same(?: thing)? as )?(?P<y>.+)$"),
    ("synonym", r"^(?:do|does) (?P<x>.+?) and (?P<y>.+?) mean the same(?: thing)?$"),
    ("synonym", r"^(?:are|is) (?P<x>.+?) and (?P<y>.+?) synonyms$"),
    ("antonym", r"^(?:is|are) (?P<x>.+?) (?:the |an )?(?:opposite|antonym) (?:of|to|for) (?P<y>.+)$"),
    ("antonym", r"^(?:are|is) (?P<x>.+?) and (?P<y>.+?) (?:opposites|antonyms)$"),
]
LEX_COUNT = {"meanings": "define", "definitions": "define", "senses": "define", "synonyms": "synonym",
             "antonyms": "antonym", "opposites": "antonym", "kinds": "hyponym", "types": "hyponym"}
LEX_INNER = [      # word phrases inside a question: "the opposite of happy", "a synonym for big"
    ("antonym", r"^(?:the |an? )?(?:opposite|antonym) (?:of|for|to) (?P<x>.+)$"),
    ("synonym", r"^(?:a |an |the )?(?:synonym|other word|another word) (?:for|of) (?P<x>.+)$"),
    ("synonym", r"^(?:a |the )?word (?:that means|meaning) (?P<x>.+)$"),
    ("hypernym", r"^(?:the |a )?hypernym of (?P<x>.+)$"),
]
NOT_A_WORD = {"that", "it", "this", "they", "them", "you", "i", "me", "we", "he", "she", "one", "those", "these"}

# Follow-ups: what to do with "another one", "what about sad", "and the opposite".
F_MORE = re.compile(r"^(?:and |ok |okay |so )?(?:another|another one|one more|any more|anymore|more|any others?|others|"
                    r"other ones|what else|anything else|(?:give me|tell me|say) another(?: one)?|another (?:word|synonym|one|"
                    r"example|kind|type|opposite)|(?:any |some )?more (?:words|synonyms|examples|kinds|types|of them|please)|"
                    r"anything more)$")
F_TELL = re.compile(r"^(?:tell me more|go on|more about (?:it|that|this)|what else do you know(?: about (?:it|that|this))?|"
                    r"tell me more about (?:it|that|this))$")
F_SENSE = re.compile(r"^(?:no,? |i mean,? |i meant,? )?(?:as in|like in|like|in the sense of|the one (?:about|for|with|like)|"
                     r"the (?:meaning|sense) (?:about|for|with)) (?P<ctx>.+)$")
F_ABOUT = re.compile(r"^(?:and |so |ok |okay )?(?:what about|how about|what of|same for|and|now) (?P<x>.+)$")
F_SWITCH = re.compile(r"^(?:and |so |ok |okay )?(?:what(?: is|'s| are)? |and |give me |tell me )?(?:the |its |a |an |some |any )?"
                      r"(?P<what>opposite|antonym|antonyms|synonyms?|other words?|another word|meaning|definition|kinds|types|"
                      r"examples|hypernym|example sentence|related words)(?: of (?:it|that|this|that word))?$")
F_THAT = re.compile(r"^(?:and |so |ok |okay )?(?:what does (?:that|this|that word|that one|it) mean|define (?:that|it|this)|"
                    r"what is that mean|meaning of that|what is the meaning of (?:that|it))$")
SWITCH_KIND = {"opposite": "antonym", "antonym": "antonym", "antonyms": "antonym", "synonym": "synonym",
               "synonyms": "synonym", "other word": "synonym", "other words": "synonym", "another word": "synonym",
               "meaning": "define", "definition": "define", "kinds": "hyponym", "types": "hyponym", "examples": "hyponym",
               "hypernym": "hypernym", "example sentence": "example", "related words": "derivation"}


@dataclass
class Context:
    """What the last answered question was about, so a follow-up can lean on it:
    "another one", "what about sad", "and the opposite?", "what does that mean?".
    Only plans and names go in here; facts always come from running a plan."""
    plan: object = None                         # the last plan that wasn't a "more" follow-up
    subject: str = ""                           # the entity it FOUND
    said: list = field(default_factory=list)    # the short names it answered with, so "another" can skip them
    kind: str = ""

    def remember(self, plan, res) -> None:
        if plan is None or res is None or res.status in ("invalid", "no_entity"):
            return
        names = [v for v in (res.final or {}).get("values", []) if len(v.split()) <= 4
                 and re.fullmatch(r"[A-Za-z0-9 '\-]+", v)] if (res.final or {}).get("kind") == "values" else []
        if plan.kind == "more" and self.plan is not None:
            self.said += [n for n in names if n not in self.said]
        else:
            self.plan, self.said = plan, names
            self.subject = res.subjects[0] if res.subjects else ""
        self.kind = plan.kind

    def clear(self) -> None:
        self.plan, self.subject, self.said, self.kind = None, "", [], ""


class RulePlanner:
    label = "rules"

    def __init__(self, corpus: Corpus, context: Context | None = None):
        self.corpus = corpus
        self.nav = Navigator(corpus)
        self.context = context

    # -------------------------------------------------------------- entity tests

    def is_entity(self, phrase: str) -> bool:
        return bool(phrase) and bool(self.nav.resolve(strip_article(phrase)))

    def is_word(self, phrase: str) -> bool:
        """A single dictionary-style name: in the corpus as written, or findable another way
        (happier -> happy, joyful -> happy, colour -> color)."""
        p = strip_article(re.sub(r"^(?:the )?word ", "", phrase.strip(" '\"")))
        if not p or p in NOT_A_WORD or len(p.split()) > 4:
            return False
        return self.is_whole_entity(p) or self.nav.soft_resolve(p) is not None

    def is_whole_entity(self, phrase: str) -> bool:
        ws = re.findall(r"[a-z0-9][a-z0-9'\-]*", strip_article(phrase))
        if not ws:
            return False
        k = "_".join(ws).upper()
        return any(self.corpus.has(x) for x in (k, k[:-1] if k.endswith("S") else "", k[:-2] if k.endswith("ES") else "",
                                                 k[:-3] + "Y" if k.endswith("IES") else ""))

    # -------------------------------------------------------------- the patterns

    def plans(self, question: str) -> list[Plan]:
        """Every reading the patterns allow, most specific first.  navigate() tries them in turn,
        so a reading that leads nowhere ("which language is spoken in france" read as a reverse
        lookup) gives way to the next one (France's language)."""
        q = normalize(question)
        if not q or not self.corpus.ready:
            return []
        out = []

        def add(ps):
            for p in (ps if isinstance(ps, list) else [ps]):
                if p and str(p) not in {str(x) for x in out}:
                    p.source = self.label
                    out.append(p)

        add(self._followup(q))                 # before "it" is replaced: "what does it mean" may mean the answer
        if self.corpus.last_subject:          # "how big is it" -> the thing we were just talking about
            name = self.corpus.last_subject.lower().replace("_", " ")
            q = re.sub(r"\bits\b", f"{name}'s", q)
            q = re.sub(r"\b(?:it|they|them)\b", name, q)
        req = self._require(q)
        if req:
            add(req)
            return out[:4]
        calc = self._calc(q)
        if calc:
            # A calculation was asked for.  Other readings would answer part of it ("the combined population of
            # France and Germany" -> France's population), which is a wrong answer, so they aren't tried.
            add(calc)
            return out[:4]
        readings = (self._lexical, self._compare, self._logic, self._test, self._count, self._reverse, self._explain,
                    self._measure, self._forward, self._chain)
        for fn in readings:
            add(fn(q))
        if not out and ", " in q:
            # No reading with the comma as a separator: it may be part of a name ("springfield, illinois", D4).
            # Only tried when nothing else planned, so a list ("compare a, b and c") reads as before.
            for fn in readings:
                add(fn(q.replace(", ", " ")))
        return out[:4]

    def plan(self, question: str) -> Plan | None:
        ps = self.plans(question)
        return ps[0] if ps else None

    def accept(self, question: str, plan: Plan, res) -> str:
        """Only the direction check: "what orbits venus" must not come out as "Venus orbits Sun" when the
        reverse lookup found nothing and fell back to Venus's own relation (computer/controller.py wrong_way)."""
        if not any("used its own relation instead" in t for t in res.steps):
            return ""
        from .controller import Reader, wrong_way
        if getattr(self, "_reader", None) is None:
            self._reader = Reader(self.corpus, self.nav)
        return wrong_way(self._reader.read(question), plan, res, self.corpus)

    def _mk(self, kind: str, lines: list[str]) -> Plan | None:
        try:
            p = parse("\n".join(lines))
        except PlanError:
            return None
        p.kind = kind
        return p

    # -------------------------------------------------------------- dictionary questions

    def _lex_path(self, phrase: str, depth: int = 0):
        """'the opposite of happy' -> ('happy', ['~antonym']);  'happy' -> ('happy', [])."""
        phrase = re.sub(r"^(?:the )?word ", "", phrase.strip(" ,'\""))
        if depth < 3:
            for kind, rx in LEX_INNER:
                m = re.match(rx, phrase)
                if m:
                    inner = self._lex_path(m["x"], depth + 1)
                    if inner:
                        return inner[0], inner[1] + [LEX[kind]]
        if self.is_word(phrase):
            return strip_article(re.sub(r"^(?:the )?word ", "", phrase)), []
        return None

    def _lex_plan(self, kind: str, phrase: str, ctx: str = "", count: bool = False):
        path = self._lex_path(phrase)
        if not path:
            return None
        ent, rels = path
        lines = [f"$1 = FIND {ent}"]
        for r in rels + [LEX[kind]]:
            lines.append(f"${len(lines) + 1} = GET ${len(lines)} {r}")
        ctx_words = [w for w in content(ctx or "") if w not in NOT_A_WORD]
        if ctx_words and kind == "define":
            lines.append(f"${len(lines) + 1} = RANK ${len(lines)} {' '.join(ctx_words)}")
        if count:
            lines.append(f"${len(lines) + 1} = COUNT ${len(lines)}")
        label = "lex_count" if count else ("lex_chain" if rels else kind)
        main = self._mk(label, lines + [f"ANSWER ${len(lines)}"])
        if kind == "hyponym" and not rels and not count:
            # also: the things the corpus says are a kind of it ("what is a kind of dog")
            return [main, self._mk(label, [f"$1 = FIND {ent}", f"$2 = WHO $1 {LEX['hypernym']}", "ANSWER $2"])]
        return main

    def _lexical(self, q):
        q = re.sub(r"[\"\u201c\u201d]", "", q)
        q = re.sub(r"^(?:what's|whats)\b", "what is", q)
        q = re.sub(r"^(?:(?:can|could|would) you (?:please )?(?:think of|give me|tell me|find|name|say|come up with)|"
                   r"(?:please|i need|i want|i'd like)(?: to know)?)\s+", "", q)
        for kind, rx in LEX_TESTS:
            m = re.match(rx, q)
            if m and self.is_word(m["x"]) and self.is_word(m["y"]):
                x, y = (strip_article(m[g]) for g in ("x", "y"))
                return [self._mk("lex_test", [f"$1 = FIND {a}", f"$2 = GET $1 {LEX[kind]}", f"$3 = TEST $2 {b}", "ANSWER $3"])
                        for a, b in ((x, y), (y, x))]
        m = re.match(r"^how many (?P<n>[a-z]+) (?:does|do|has|have) (?:the word )?(?P<x>.+?)(?: have| has| got)?$", q)
        if m and m["n"] in LEX_COUNT:
            return self._lex_plan(LEX_COUNT[m["n"]], m["x"], count=True)
        for kind, rx in LEX_PATTERNS:
            m = re.match(rx, q)
            if m:
                p = self._lex_plan(kind, m["x"], (m.groupdict().get("ctx") or ""))
                if p:
                    return p
        return None

    def _followup(self, q):
        """A question that only makes sense after the last one."""
        c = self.context
        if c is None or c.plan is None:
            return None
        base = [str(s) for s in c.plan.steps]
        if F_MORE.match(q):
            if not c.said or c.plan.steps[-1].op != "ANSWER":
                return None
            body, ans = base[:-1], c.plan.steps[-1].args[-1]
            n = len(body) + 1
            if n > 9:
                return None
            return self._mk("more", body + [f"${n} = EXCEPT {ans} {', '.join(w.lower() for w in c.said)}", f"ANSWER ${n}"])
        subject = c.subject.lower().replace("_", " ")
        if F_TELL.match(q) and subject:
            lines = [f"$1 = FIND {subject}", "$2 = GET $1 *"]
            if c.said:
                lines.append(f"$3 = EXCEPT $2 {', '.join(w.lower() for w in c.said)}")
            return self._mk("switch", lines + [f"ANSWER ${len(lines)}"])
        defined = any(st.op == "GET" and st.args[1] == LEX["define"] for st in c.plan.steps)
        m = F_SENSE.match(q) or (F_ABOUT.match(q) if defined else None)
        if m and defined and subject:
            ctx = m.groupdict().get("ctx") or m.groupdict().get("x") or ""
            if F_SENSE.match(q) or not (self.is_whole_entity(strip_article(ctx)) or self.is_word(ctx)
                                        or ctx.split()[0] in WH):
                p = self._lex_plan("define", subject, ctx)        # "what does bank mean" ... "what about money?"
                if p and any(st.op == "RANK" for st in p.steps):
                    p.kind = "switch"
                    return p
        m = F_THAT.match(q)
        if m:
            words = [w for w in c.said if self.is_whole_entity(w)]
            word = words[0] if len(words) == 1 else subject
            return self._lex_plan("define", word) if word else None
        m = F_SWITCH.match(q)
        if m and subject:
            p = self._lex_plan(SWITCH_KIND[m["what"]], subject)
            if p:
                for x in (p if isinstance(p, list) else [p]):
                    x.kind = "switch"
            return p
        m = F_ABOUT.match(q)
        if m and sum(s.op == "FIND" for s in c.plan.steps) == 1 and c.plan.steps[0].op == "FIND":
            x = strip_article(m["x"])
            if x.split()[0] in WH or x.split()[0] in AUX or not (self.is_whole_entity(x) or self.is_word(x)):
                return None         # "and what is the population of spain" is a new question, not "what about spain"
            steps = c.plan.steps
            if len(steps) > 2 and steps[-2].op in ("RANK", "EXCEPT"):      # a sense or a skip list was for the old word
                body = base[1:-2] + [f"ANSWER {steps[-2].args[0]}"]
            else:
                body = base[1:]
            return self._mk("about", [f"$1 = FIND {x}"] + body)
        return None

    def _compare(self, q):
        pats = [
            r"^(?:which|who|what)(?: one)? (?:is|was|are|were|has|have) (?:the )?(?P<dir>greater|larger|bigger|higher|"
            r"smaller|lower|more|fewer|less) (?P<attr>[a-z ]+?),? (?P<a>[^,]+?),? or (?P<b>.+)$",
            r"^(?:which|who|what)(?: one)? (?:is|was|are|were) (?P<adj>(?:more|less) [a-z]+|[a-z]+er)(?: one)?,? "
            r"(?P<a>[^,]+?),? or (?P<b>.+)$",
            r"^(?:is|are|was|were) (?P<a>.+?) (?P<adj>(?:more|less) [a-z]+|[a-z]+er) than (?P<b>.+)$",
            r"^(?:does|do|did|has|have) (?P<a>.+?) (?:have |got )?(?P<dir>more|fewer|less) (?P<attr>[a-z ]+?) than (?P<b>.+)$",
            r"^compare (?:the )?(?P<attr>[a-z ]+?) of (?P<a>.+?) (?:and|with|to|vs) (?P<b>.+)$",
        ]
        for pat in pats:
            m = re.match(pat, q)
            if not m:
                continue
            g = m.groupdict()
            a, b = strip_article(g["a"]), strip_article(g["b"])
            if not (self.is_entity(a) and self.is_entity(b)):
                continue
            if g.get("adj"):
                adj = g["adj"]
                if adj.startswith(("more ", "less ")):
                    word, base = adj.split()[0], adj.split()[1]
                else:
                    word = adj
                    base = COMPARATIVE.get(adj)
                    if base is None:
                        base = re.sub(r"(.)\1er$", r"\1", adj) if re.search(r"(.)\1er$", adj) else re.sub(r"i?er$", "", adj)
                    if not base:
                        continue
                rel, label = "~" + base, adj
                less = word in LESS_WORDS or adj in LESS_WORDS
            else:
                rel = rel_of(g["attr"])
                if not rel:
                    continue
                d = g.get("dir") or "more"
                less = d in LESS_WORDS
                label = d if d in ("more", "fewer", "less") else ""
            return self._mk("compare", [f"$1 = FIND {a}", f"$2 = FIND {b}", f"$3 = GET $1 {rel}",
                                        f"$4 = GET $2 {rel}", f"$5 = COMPARE $3 $4 {'LESS' if less else 'MORE'} {label}".rstrip(),
                                        "ANSWER $5"])
        return None

    def _require(self, q):
        """A requirement check: "does battery alpha meet 50 wh", "does X have at least 40 Wh", "which of alpha and
        beta meets 50 wh".  The stated figure must parse as one quantity with a unit; its dimension is what MEASURE
        looks for, so the validator can check the two agree."""
        from .quantity import parse as parse_qty
        verbs = r"(?:meets?|satisf(?:y|ies)|fulfil(?:l|ls|s)?|reach(?:es)?|pass(?:es)?|clears?)"
        tail = r"(?:the |a |an )?(?:requirement|minimum|target|threshold|spec|limit)?(?: of)? ?(?:at least )?(?P<q>.+?)" \
               r"(?: requirement| minimum| target| threshold)?$"
        one = [re.match(r"^(?:does|do|did|will|would|can|could) (?P<x>.+?) " + verbs + " " + tail, q),
               re.match(r"^(?:does|do|did|will|would|can|could) (?P<x>.+?) (?:have|hold|store|provide|give|deliver) "
                        r"at (?P<cmp>least|most) (?P<q>.+)$", q),
               re.match(r"^(?:is|are) (?P<x>.+?) (?:rated )?at (?P<cmp>least|most) (?P<q>.+)$", q)]
        many = re.match(r"^(?:which|what)(?: one| ones| battery| batteries| of them)?(?: of)? (?P<list>.+?) "
                        + verbs + " " + tail, q)

        def limit(text):
            qs = parse_qty(text)
            if len(qs) != 1:
                return None
            num, unit = qs[0].text.split(" ", 1)
            from .quantity import unit_dimension
            d = unit_dimension(unit)
            return (num.replace(",", ""), unit, d) if d else None

        m = next((x for x in one if x), None)
        if m:
            lim = limit(m["q"])
            name, note = self._requirement_name(m["x"])
            if lim and name:
                cmp_ = "AT_MOST" if m.groupdict().get("cmp") == "most" else "AT_LEAST"
                adj = [w for w in re.findall(r"[a-z]+", m["q"]) if w in DIMENSIONS]       # "6000 km long" -> long
                p = self._mk("require", [f"$1 = FIND {name}", f"$2 = MEASURE $1 {lim[2]}" + (f" ~{','.join(adj)}" if adj else ""),
                                         f"$3 = REQUIRE $2 {cmp_} {lim[0]} {lim[1]}", "ANSWER $3"])
                if p and note:
                    p.notes = [note]
                return p
        if many:
            lim = limit(many["q"])
            raw = [x for x in re.split(r",\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+", many["list"]) if x.strip()]
            got = [self._requirement_name(x) for x in raw] if 2 <= len(raw) <= 3 else []
            if lim and got and all(n for n, _ in got):
                k = len(got)
                lines = [f"${i + 1} = FIND {n}" for i, (n, _) in enumerate(got)]
                lines += [f"${k + i + 1} = MEASURE ${i + 1} {lim[2]}" for i in range(k)]
                lines += [f"${2 * k + i + 1} = REQUIRE ${k + i + 1} AT_LEAST {lim[0]} {lim[1]}" for i in range(k)]
                lines += [f"${3 * k + 1} = SELECT " + " ".join(f"${2 * k + i + 1}" for i in range(k)), f"ANSWER ${3 * k + 1}"]
                p = self._mk("require", lines)
                if p:
                    p.notes = [n for _, n in got if n]
                return p
        return None

    def _requirement_name(self, phrase: str):
        """(name for FIND, note) for a thing in a requirement question.  A name the corpus has is used as it is.
        Otherwise a short name ("alpha") is read as the one subject whose name ends with it (BATTERY_ALPHA), and
        the answer says so; none, or more than one, and it isn't guessed."""
        x = strip_article(phrase.strip().replace("_", " "))      # "BATTERY_ALPHA", as typed, is "battery alpha"
        if self.is_entity(x):
            return x, ""
        words = re.findall(r"[a-z0-9]+", x)
        if not words or len(words) > 3:
            return None, ""
        tail = "_" + "_".join(words).upper()
        hits = [k for k in self.corpus.subjects if k.endswith(tail)]
        if len(hits) != 1:
            return None, ""
        full = hits[0].lower().replace("_", " ")
        return full, f"I took {x.title()} to mean {full.title()}."

    def _calc(self, q):
        """Arithmetic over corpus numbers: "how many times heavier is jupiter than saturn" (RATIO), "the combined
        population of france and germany" (SUM), "the difference in height between A and B" / "how much taller is
        A than B" (DIFF), "the population density of france" (population DIV area)."""
        wh = r"^(?:(?:what|how much) (?:is|was|are|were) |what's |tell me |calculate |compute |work out )?(?:the )?"

        def ents(*names):
            xs = [strip_article(n.strip()) for n in names]
            return xs if all(self.is_entity(x) for x in xs) else None

        def base_of(adj):
            if adj.startswith(("more ", "less ")):
                w = adj.split()[1]
                return CALC_ADJ.get(w, w)
            b = COMPARATIVE.get(adj)
            if b is None:
                b = re.sub(r"(.)\1er$", r"\1", adj) if re.search(r"(.)\1er$", adj) else re.sub(r"i?er$", "", adj)
            return b

        def two(kind, a, b, rel, label="", rel_b=""):
            # A second attribute ("the mass of A to the radius of B") is kept, never replaced by the first: the
            # executor then refuses to combine two different measurements.
            return self._mk("calc", [f"$1 = FIND {a}", f"$2 = FIND {b}", f"$3 = GET $1 {rel}", f"$4 = GET $2 {rel_b or rel}",
                                     f"$5 = CALC {kind} $3 $4 {label}".rstrip(), "ANSWER $5"])

        m = re.match(r"^how many times (?P<adj>(?:more|less) [a-z]+|[a-z]+er) (?:is|are|was|were) (?P<a>.+?) than (?P<b>.+)$", q)
        if m:
            e, base = ents(m["a"], m["b"]), base_of(m["adj"])
            if e and base:
                return two("RATIO", *e, "~" + base, m["adj"])
        m = re.match(wh + r"ratio (?:of|between) (?:the )?(?P<attr>[a-z ]+?) of (?P<a>.+?) (?:to|and) "
                          r"(?:that of |(?:the |its )?(?P<attr2>[a-z ]+?) of )?(?P<b>.+)$", q)
        if m and rel_of(m["attr"]):
            e = ents(m["a"], m["b"])
            if e:
                return two("RATIO", *e, rel_of(m["attr"]), rel_b=rel_of(m["attr2"] or ""))
        m = re.match(r"^how much (?P<adj>(?:more|less) [a-z]+|[a-z]+er) (?:is|are|was|were) (?P<a>.+?) than (?P<b>.+)$", q)
        if m:
            e, base = ents(m["a"], m["b"]), base_of(m["adj"])
            if e and base:
                return two("DIFF", *e, "~" + base, m["adj"])
        m = re.match(wh + r"difference (?:in|of) (?:the )?(?P<attr>[a-z ]+?) (?:between|of) (?P<a>.+?) and (?P<b>.+)$", q) or \
            re.match(wh + r"difference between (?:the )?(?P<attr>[a-z ]+?) of (?P<a>.+?) and "
                          r"(?:that of |(?:the |its )?(?P<attr2>[a-z ]+?) of )?(?P<b>.+)$", q)
        if m and rel_of(m["attr"]):
            e = ents(m["a"], m["b"])
            if e:
                return two("DIFF", *e, rel_of(m["attr"]), rel_b=rel_of(m.groupdict().get("attr2") or ""))
        m = re.match(wh + r"(?:combined|total|joint|sum of the|summed) (?P<attr>[a-z ]+?) of (?P<list>.+)$", q) or \
            re.match(wh + r"(?P<attr>[a-z ]+?) of (?P<list>.+?) (?:combined|together|in total|altogether|added together)$", q) or \
            re.match(r"^add (?:up )?(?:the )?(?P<attr>[a-z ]+?) of (?P<list>.+)$", q) or \
            re.match(wh + r"(?P<list>.+?)'s? (?:combined |total )?(?P<attr>[a-z]+(?: [a-z]+)?) (?:combined|together|in total|"
                          r"altogether|added together)$", q)
        if m and rel_of(m["attr"]):
            names = [x for x in re.split(r",\s*(?:and\s+)?|\s+and\s+", m["list"]) if x.strip()]
            e = ents(*names) if 2 <= len(names) <= 3 else None
            if e:
                rel = rel_of(m["attr"])
                lines = [f"${i + 1} = FIND {x}" for i, x in enumerate(e)]
                n = len(e)
                lines += [f"${n + i + 1} = GET ${i + 1} {rel}" for i in range(n)]
                lines += [f"${2 * n + 1} = CALC SUM " + " ".join(f"${n + i + 1}" for i in range(n)), f"ANSWER ${2 * n + 1}"]
                return self._mk("calc", lines)
        m = re.match(wh + r"population density of (?P<a>.+)$", q) or re.match(r"^how densely populated (?:is|are) (?P<a>.+)$", q)
        if m:
            e = ents(m["a"])
            if e:
                return self._mk("calc", [f"$1 = FIND {e[0]}", "$2 = GET $1 ~population", "$3 = GET $1 ~area",
                                         "$4 = CALC DIV $2 $3 its population density", "ANSWER $4"])
        return None

    def _logic(self, q):
        """Yes/no questions with and / or / neither-nor / not, and "is the capital of france lyon",
        "does titan orbit jupiter": answered TRUE, FALSE (a knowledge rule), UNKNOWN or CONFLICT."""
        art = r"(?:a |an |the )?"
        tests = lambda x, ys, rel: [f"$1 = FIND {x}", f"$2 = GET $1 {rel}"] + \
            [f"${i + 3} = TEST $2 {y}" for i, y in enumerate(ys)]
        m = re.match(rf"^(?:is|are|was|were) {art}(?P<x>.+?) neither {art}(?P<y>.+?) nor {art}(?P<z>.+)$", q)
        op = "NOR"
        if not m:
            m = (re.match(rf"^(?:is|are|was|were) {art}(?P<x>.+?) (?:a|an) (?P<y>.+?) (?P<op>and|or) {art}(?P<z>.+)$", q)
                 or re.match(r"^(?:is|are|was|were) (?P<x>[a-z]+s) (?P<y>[a-z]+s) (?P<op>and|or) (?P<z>[a-z]+)$", q))
            op = m["op"].upper() if m else ""
        if m and self.is_entity(m["x"]) and content(m["y"]) and content(m["z"]) and " and " not in m["y"]:
            x, y, z = strip_article(m["x"]), strip_article(m["y"]), strip_article(m["z"])
            return [self._mk("logic", tests(x, [y, z], rel) + [f"$5 = LOGIC {op} $3 $4", "ANSWER $5"])
                    for rel in ("IS_A", LEX["hypernym"])]
        m = re.match(rf"^(?:is|are|was|were) {art}(?P<x>.+?) not {art}(?P<y>.+)$", q)
        if m and self.is_entity(m["x"]) and content(m["y"]):
            x, y = strip_article(m["x"]), strip_article(m["y"])
            return [self._mk("logic", tests(x, [y], rel) + ["$4 = LOGIC NOT $3", "ANSWER $4"])
                    for rel in ("IS_A", LEX["hypernym"])]
        # "is the capital of france paris": the relation of x, then is y among its values
        m = re.match(r"^(?:is|was) (?:the )?(?P<r>[a-z]+) of (?P<rest>.+)$", q)
        if m and m["r"] not in GENERIC:
            # where the name ends: longest first ("cape verde" + "praia", not "cape" + "verde praia")
            toks, out = m["rest"].split(), []
            for i in range(len(toks) - 1, 0, -1):
                x, y = " ".join(toks[:i]), " ".join(toks[i:])
                if self.is_whole_entity(x) and self.is_entity(y):
                    out.append(self._mk("test", [f"$1 = FIND {strip_article(x)}", f"$2 = GET $1 ~{m['r']}",
                                                 f"$3 = TEST $2 {strip_article(y)}", "ANSWER $3"]))
            if out:
                return out[:3]
        # "is paris the capital of france": y, then the relation of x
        m = re.match(r"^(?:is|was) (?P<y>.+?) (?:the )?(?P<r>[a-z]+) of (?P<x>.+)$", q)
        if m and m["r"] not in GENERIC and self.is_entity(m["y"]) and self.is_whole_entity(m["x"]) \
                and not re.match(r"(?:a|an) ", m["y"]):
            return self._mk("test", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 ~{m['r']}",
                                     f"$3 = TEST $2 {strip_article(m['y'])}", "ANSWER $3"])
        # "does titan orbit jupiter": x, a verb, y
        m = re.match(r"^(?:does|do|did) (?P<rest>.+)$", q)
        if m:
            toks, out = m["rest"].split(), []
            for i in range(len(toks) - 2, 0, -1):
                x, verb, y = " ".join(toks[:i]), toks[i], " ".join(toks[i + 1:])
                if verb in ("have", "has", "got", "own", "eat", "eats") or not verb.isalpha() or len(verb) < 2:
                    continue                  # one letter is never the verb: "proxima centauri b" is a name
                if self.is_whole_entity(x) and self.is_entity(y):
                    out.append(self._mk("test", [f"$1 = FIND {strip_article(x)}", f"$2 = GET $1 ~{verb}",
                                                 f"$3 = TEST $2 {strip_article(y)}", "ANSWER $3"]))
            if out:
                return out[:3]
        return None

    def _test(self, q):
        m = (re.match(r"^(?:is|are|was|were) (?P<x>.+?) (?:a |an )?(?:kind|type|sort) of (?P<y>.+)$", q)
             or re.match(r"^(?:is|are|was|were) (?:a |an |the )?(?P<x>.+?) (?:a|an) (?P<y>.+)$", q)
             or re.match(r"^(?:is|are) (?P<x>[a-z]+s) (?P<y>[a-z]+s)$", q))
        if m and self.is_entity(m["x"]) and content(m["y"]):
            x, y = strip_article(m["x"]), strip_article(m["y"])
            return [self._mk("test", [f"$1 = FIND {x}", "$2 = GET $1 IS_A", f"$3 = TEST $2 {y}", "ANSWER $3"]),
                    # the dictionary's "a kind of" (HYPERNYM) when the curated IS_A doesn't link them
                    self._mk("test", [f"$1 = FIND {x}", f"$2 = GET $1 {LEX['hypernym']}", f"$3 = TEST $2 {y}", "ANSWER $3"])]
        m = re.match(r"^(?:is|are|was|were) (?P<x>.+?) (?:located |found |situated )?(?:in|inside|part of|on) (?P<y>.+)$", q)
        if m and self.is_entity(m["x"]) and self.is_entity(m["y"]):
            return self._mk("test", [f"$1 = FIND {strip_article(m['x'])}", "$2 = GET $1 ~located,part",
                                     f"$3 = TEST $2 {strip_article(m['y'])}", "ANSWER $3"])
        m = re.match(r"^(?:does|do|did|can) (?P<x>.+?) (?:have|has|got) (?:a |an |any |the |some )?(?P<y>.+)$", q)
        if m and self.is_entity(m["x"]) and content(m["y"]) and not re.match(r"(more|fewer|less) ", m["y"]):
            return self._mk("test", [f"$1 = FIND {strip_article(m['x'])}", "$2 = GET $1 * INHERIT",
                                     f"$3 = TEST $2 {strip_article(m['y'])}", "ANSWER $3"])
        return None

    def _count(self, q):
        m = re.match(r"^how many (?P<attr>[a-z ]+?) (?:does|do|did) (?P<x>.+?) (?P<verb>[a-z]+)$", q)
        if m and m["verb"] not in ("have", "has", "got", "own") and self.is_whole_entity(m["x"]):
            attr = [w for w in content(m["attr"]) if w not in GENERIC]
            verb = re.sub(r"(?<=[a-z]{3})s$", "", m["verb"])
            r = "~" + ",".join([verb] + attr)
            return self._mk("count", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 {r}", "$3 = COUNT $2", "ANSWER $3"])
        m = re.match(r"^how many (?P<attr>[a-z ]+?) (?:does|do|did|has|have) (?P<x>.+?)(?: have| got| has)?$", q)
        if m and self.is_entity(m["x"]):
            r = rel_of(m["attr"])
            if r:
                return self._mk("count", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 {r}", "$3 = COUNT $2", "ANSWER $3"])
        m = re.match(r"^how many (?P<attr>[a-z ]+?) (?:are there |live |are |is |lived )?(?:in|on|at|of) (?P<x>.+)$", q)
        if m:
            r = rel_of(m["attr"])
            path = self._path(m["x"])
            if r and path:
                ent, attrs = path
                lines = [f"$1 = FIND {ent}"]
                for i, a in enumerate(attrs + [r]):
                    lines.append(f"${i + 2} = GET ${i + 1} {a}")
                n = len(lines)
                return self._mk("count", lines + [f"${n + 1} = COUNT ${n}", f"ANSWER ${n + 1}"])
        return None

    def _reverse(self, q):
        tok = q.split()
        if len(tok) < 3 or tok[0] not in ("what", "which", "who", "name"):
            return None
        for skip in (0, 1):
            rest = tok[1 + skip:]
            if len(rest) < 2:
                continue
            typ = tok[1] if skip else ""
            if typ and (typ in AUX or typ in STOP):
                continue
            i = 0
            if rest[0] in ("is", "are", "was", "were"):
                if len(rest) < 3 or rest[1] not in PARTICIPLE:
                    continue
                i = 1
            verb = rest[i]
            if verb in AUX or verb in STOP or verb in WH:
                continue
            j = i + 1
            if j < len(rest) and rest[j] in ("in", "on", "to", "by", "with", "from", "of", "around", "after"):
                j += 1
            x = " ".join(rest[j:])
            if not x or not self.is_whole_entity(x):
                continue
            stem = verb_stem(verb)
            lines = [f"$1 = FIND {strip_article(x)}", f"$2 = WHO $1 ~{stem}"]
            if typ:
                lines.append(f"$3 = FILTER $2 {re.sub(r'(ies)$', 'y', typ) if typ.endswith('ies') else typ.rstrip('s')}")
            lines.append(f"ANSWER ${len(lines)}")
            return self._mk("reverse", lines)
        return None

    def _explain(self, q):
        m = re.match(r"^(?:explain |tell me )?(?:how|why) (?:do|does|did|can|is|are) (?P<x>.+?) (?P<verb>[a-z]+)$", q)
        if m and self.is_entity(m["x"]) and m["verb"] not in STOP:
            return self._mk("explain", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 ~{m['verb']} INHERIT", "ANSWER $2"])
        return None

    def _measure(self, q):
        m = re.match(r"^how (?P<adj>[a-z]+) (?:is|are|was|were) (?P<x>.+)$", q)
        if m and m["adj"] in DIMENSIONS:
            path = self._path(m["x"])
            if path:
                ent, attrs = path
                lines = [f"$1 = FIND {ent}"] + [f"${i + 2} = GET ${i + 1} {a}" for i, a in enumerate(attrs + ["~" + m["adj"]])]
                return self._mk("chain" if attrs else "measure", lines + [f"ANSWER ${len(lines)}"])
        m = re.match(r"^where (?:is|are|was|were) (?P<x>.+?)(?: located| found)?$", q)
        if m and self.is_entity(m["x"]):
            return self._mk("hop", [f"$1 = FIND {strip_article(m['x'])}", "$2 = GET $1 ~where", "ANSWER $2"])
        m = re.match(r"^when (?:was|were|is|did) (?P<x>.+?) (?P<verb>[a-z]+)$", q)
        if m and self.is_entity(m["x"]):
            return self._mk("hop", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 ~when,{m['verb']}", "ANSWER $2"])
        return None

    def _forward(self, q):
        m = re.match(r"^(?:what|which|who|whom) (?:does|do|did) (?P<x>.+?) (?P<verb>[a-z]+)"
                     r"(?: (?:in|on|to|with|from|by|of|around|for))?$", q)
        if m and self.is_entity(m["x"]) and m["verb"] not in STOP:
            return self._mk("hop", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 ~{m['verb']} INHERIT", "ANSWER $2"])
        # "what is the blowhole used for", "what are lungs part of"
        m = re.match(r"^(?:what|which|who|where) (?:is|are|was|were) (?P<x>.+?) (?P<p>part|used|made|named|located|"
                     r"member|founded|built|owned|composed|found)(?: (?:of|for|by|in|after|from|at))?$", q)
        if m and self.is_entity(m["x"]):
            return self._mk("hop", [f"$1 = FIND {strip_article(m['x'])}", f"$2 = GET $1 ~{m['p']}", "ANSWER $2"])
        # "what currency does france use", "which language is spoken in france"
        m = re.match(r"^(?:what|which) (?P<attr>[a-z]+) (?:does|do|did|is|are|was|were) (?P<x>.+?)(?: (?P<verb>[a-z]+))?$", q)
        if m and m["attr"] not in AUX and m["attr"] not in STOP:
            for x, verb in ((m["x"], m["verb"]), (f"{m['x']} {m['verb'] or ''}".strip(), None)):
                x = re.sub(r"^(?:spoken |used |found )?(?:in|on|by) ", "", x)
                if self.is_whole_entity(x):
                    rel = rel_of(m["attr"])
                    return self._mk("hop", [f"$1 = FIND {strip_article(x)}", f"$2 = GET $1 {rel}", "ANSWER $2"])
        return None

    def _chain(self, q):
        q = re.sub(r"^(?:what|who|which|where|when)(?:'s| is| are| was| were)? ", "", q)
        q = re.sub(r"^(?:tell me|name|give me|say) ", "", q)
        path = self._path(q)
        if not path:
            return None
        ent, attrs = path
        if not attrs:
            # "what is a puppy": what the corpus says it is, else what the dictionary says it means
            return [self._mk("lookup", [f"$1 = FIND {ent}", "$2 = GET $1 IS_A", "ANSWER $2"]),
                    self._mk("lookup", [f"$1 = FIND {ent}", f"$2 = GET $1 {LEX['define']}", "ANSWER $2"])]
        lines = [f"$1 = FIND {ent}"] + [f"${i + 2} = GET ${i + 1} {a}" for i, a in enumerate(attrs)]
        return self._mk("chain" if len(attrs) > 1 else "hop", lines + [f"ANSWER ${len(lines)}"])

    def _path(self, phrase: str):
        """'the population of the capital of france' -> ('france', ['~capital', '~population'])."""
        phrase = self._unpossess(strip_article(phrase))
        parts = re.split(r" (of|in|for) ", phrase)
        segs = parts[0::2]
        joins = parts[1::2]
        # A relation whose name has "of" in it ("number of moons") is one attribute, not two hops.
        rels = set((self.corpus.meta or {}).get("relation_names", []))
        i = 0
        while i < len(segs) - 1:
            merged = f"{segs[i]} {joins[i]} {segs[i + 1]}"
            name = "_".join(content(merged) if joins[i] != "of" else
                            [w for w in re.findall(r"[a-z]+", merged) if w not in ("the", "a", "an", "what", "is")]).upper()
            if name in rels or "HAS_" + name in rels:
                segs[i:i + 2] = [merged]
                del joins[i]
            else:
                i += 1
        for i in range(len(segs)):
            tail = segs[i] + "".join(f" {j} {s}" for j, s in zip(joins[i:], segs[i + 1:]))
            if self.is_whole_entity(tail):
                attrs = [rel_of(s) for s in segs[:i]]
                if all(attrs) and len(attrs) <= 4:
                    return strip_article(tail), attrs[::-1]
        if len(segs) > 1 and self.is_entity(segs[-1]):
            attrs = [rel_of(s) for s in segs[:-1]]
            if all(attrs) and len(attrs) <= 4:
                return strip_article(segs[-1]), attrs[::-1]
        if len(segs) == 1 and self.is_whole_entity(segs[0]):
            return strip_article(segs[0]), []
        return None

    def _unpossess(self, phrase: str) -> str:
        """"france's capital's population" -> "population of capital of france"."""
        if "'s " not in phrase:
            return phrase
        parts = phrase.split("'s ")
        head = parts[0].split()
        for i in range(len(head)):
            cand = " ".join(head[i:])
            if self.is_whole_entity(cand):
                lead = " ".join(head[:i])
                chain = cand
                for p in parts[1:]:
                    chain = f"{p} of {chain}"
                return (lead + " " + chain).strip()
        return phrase


# ---------------------------------------------------------------------- the small model

FEWSHOT = """Q: what is the capital of france
$1 = FIND france
$2 = GET $1 ~capital
ANSWER $2
Q: what is the population of the capital of germany
$1 = FIND germany
$2 = GET $1 ~capital
$3 = GET $2 ~population
ANSWER $3
Q: which is heavier, mars or venus
$1 = FIND mars
$2 = FIND venus
$3 = GET $1 ~heavy
$4 = GET $2 ~heavy
$5 = COMPARE $3 $4 MORE heavier
ANSWER $5
Q: what orbits the sun
$1 = FIND sun
$2 = WHO $1 ~orbit
ANSWER $2
Q: is a dolphin a mammal
$1 = FIND dolphin
$2 = GET $1 IS_A
$3 = TEST $2 mammal
ANSWER $3
Q: how do frogs breathe
$1 = FIND frogs
$2 = GET $1 ~breathe INHERIT
ANSWER $2
Q: how many moons does mars have
$1 = FIND mars
$2 = GET $1 ~moons
$3 = COUNT $2
ANSWER $3
Q: what is another word for big
$1 = FIND big
$2 = GET $1 ~synonym,similar
ANSWER $2
Q: what does bank mean, as in a river
$1 = FIND bank
$2 = GET $1 ~definition
$3 = RANK $2 river
ANSWER $3"""

LM_SYSTEM = ("You turn a question into a plan for a knowledge graph. Use only these steps: FIND, GET, WHO, FILTER, "
             "COMPARE, COUNT, TEST, LOGIC, RANK, EXCEPT, CHECK, ANSWER. Write the plan and nothing else.")


class LMPlanner:
    """A small model writes the plan.  Use one fine-tuned on tools/make_navigator_data.py output,
    or any instruct model with the few-shot prompt (weaker).  `grammar` constrains llama-server's
    sampler to the plan language, so the output always parses; the validator still checks it."""

    def __init__(self, llm, corpus: Corpus | None = None, fewshot: bool = True, grammar: bool = True):
        self.llm = llm
        self.corpus = corpus
        self.fewshot = fewshot
        self.grammar = grammar
        self.last_raw = ""
        self.last_error = ""

    @property
    def label(self) -> str:
        return getattr(self.llm, "label", "lm")

    def prompt(self, question: str) -> list[dict]:
        user = (FEWSHOT + "\n" if self.fewshot else "") + f"Q: {normalize(question)}\n"
        return [{"role": "system", "content": LM_SYSTEM}, {"role": "user", "content": user}]

    def plan(self, question: str) -> Plan | None:
        self.last_raw = self.last_error = ""
        try:
            extra = {"grammar": GRAMMAR} if self.grammar else None
            raw = self.llm.chat(self.prompt(question), temperature=0.0, max_tokens=140, extra=extra)
        except Exception as e:  # noqa: BLE001 - no model, no plan; the rules take over
            self.last_error = f"model failed: {e}"
            return None
        self.last_raw = raw
        text = "\n".join(l for l in raw.strip().splitlines() if l.strip() and not l.startswith("Q:"))
        if "ANSWER" in text:      # stop at the first ANSWER line
            lines = text.splitlines()
            text = "\n".join(lines[: next(i for i, l in enumerate(lines) if "ANSWER" in l) + 1])
        rels = set((self.corpus.meta or {}).get("relation_names", [])) if self.corpus else set()
        try:
            p = parse(text, rels or None)
        except PlanError as e:
            self.last_error = f"invalid plan: {e}"
            return None
        p.source, p.kind = self.label, "lm"
        return p


# ---------------------------------------------------------------------- the loop

def navigate(question: str, planners: list, navigator: Navigator, year: int = 0, trace: list | None = None,
             limit: int = 6):
    """Plan, run, and fall back to the next planner when a plan is invalid or finds nothing.

    A planner with an accept(question, plan, result) method (the learned controller) has each answer
    checked: if accept() gives a reason, the answer isn't said and the next plan is tried.

    Returns (plan, result) for the first plan that answered, else for the first plan that ran,
    else (None, None)."""
    trace = trace if trace is not None else []
    year = year or year_in(question)
    first = None
    candidates = []
    for pl in planners:
        ps = pl.plans(question) if hasattr(pl, "plans") else [p for p in [pl.plan(question)] if p]
        if not ps:
            err = getattr(pl, "last_error", "")
            trace.append(f"planner {pl.label}: no plan" + (f" ({err})" if err else ""))
        candidates += [(pl, p) for p in ps]
    for pl, p in candidates[:limit]:
        r = navigator.run(p, year=year)
        trace.append(f"planner {pl.label} [{p.kind}]: " + " | ".join(str(s) for s in p.steps))
        trace += [f"  {s}" for s in r.steps]
        trace.append(f"  => {r.status}" + (f", confidence {r.confidence:.2f}" if r.confidence is not None else ""))
        why = pl.accept(question, p, r) if r.answered and hasattr(pl, "accept") else ""
        if why:
            # A learned planner's plan found something, but not about everything that was asked: not said.
            trace.append(f"  => rejected: {why}")
            who = next((s for s in p.steps if s.op == "WHO"), None)
            if getattr(pl, "label", "") == "rules" and who is not None and r.subjects:
                # The rules check only the direction: nothing points at the thing, and its own fact answers the
                # opposite question ("Venus orbits Sun" for "what orbits venus").  That's a finding, not a non-result,
                # so the one-shot lookup can't come back and say the opposite fact either.
                r = type(r)("unsupported", f"My corpus doesn't say what {rel_phrase(who.args[1])} {name_of(r.subjects[0])}.",
                            plan=p, steps=r.steps, subjects=r.subjects, year=r.year)
            else:
                r = type(r)("invalid", "", plan=p, steps=r.steps)
        if r.answered or r.status == "conflict":
            return p, r          # a conflict is a finding: another reading mustn't paper over it
        if p.kind in STOP_KINDS and r.status in ("unsupported", "unavailable"):
            return p, r          # a calculation that can't be done is said; a part-answer from another reading isn't
        if first is None and r.status != "invalid":
            first = (p, r)
    return first or (None, None)
