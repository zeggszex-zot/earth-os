#!/usr/bin/env python3
"""Phase 3: make question -> plan training data from your corpus.

    python3 tools/make_navigator_data.py --corpus data --subjects 20000 --out navdata

writes
    navdata/examples.jsonl          every kept example: question, plan, evidence (stated or inherited), rules,
                                    inference steps, final answer, the computer's explanation, concepts, kind, split
    navdata/train.jsonl, dev.jsonl  chat rows for fine-tuning (only the assistant's reply is learned)
    navdata/test.jsonl              held-out examples for tools/eval_navigator.py (subjects unseen in train)

Every example's plan was executed against the corpus and reached its gold answer, or it was dropped.
The split is leak-free (computer/navdata.leak_free): no training example mentions a held-out subject anywhere.
--no-truths leaves out the FALSE / and / or / not / "does titan orbit jupiter" examples (as before).

--format plan|propose|direct    what the model learns to write (see computer/navdata.py as_chat).  For
                                "propose" and "direct" with --paraphrase, the bigger model also re-words
                                the answers, and a re-wording is kept only if it passes computer/proposer.py's check.
--paraphrase http://host:8080   optional: a bigger model on an OpenAI-compatible server rewrites each
                                question in everyday words (names kept), so the navigator learns more
                                than the templates.  The plan stays the same; rewrites that lose a name are dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.corpus import Corpus           # noqa: E402
from computer.navdata import as_chat, generate, leak_free  # noqa: E402


def paraphrase_answers(examples, endpoint, corpus):
    """Computer-voiced answers from a bigger model, kept only if they pass the same check the computer uses
    at run time (computer/proposer.py: every number and name in the evidence, the answer itself said)."""
    from computer.llm import MiniLM
    from computer.navigator import Navigator
    from computer.plan import parse
    from computer.proposer import check
    lm, nav = MiniLM(endpoint, timeout=60), Navigator(corpus)
    kept = 0
    for ex in examples:
        if ex["expect"] != "answered":
            continue
        try:
            say = lm.chat([{"role": "user", "content": "You are a precise reasoning computer. Answer the question in one or two "
                            "short sentences using ONLY these facts. Keep names and numbers exactly as written.\n\n"
                            f"Question: {ex['question']}\nFacts: {ex['answer']}"}], temperature=0.7, max_tokens=80)
        except Exception as e:  # noqa: BLE001
            print(f"answer paraphrase failed: {e}", file=sys.stderr)
            return kept
        say = " ".join(say.split()).strip('"')
        if not check(say, nav.run(parse(ex["plan"])), ex["question"]):
            ex["say"] = say
            kept += 1
    return kept


def paraphrase(examples, endpoint, per_example=1):
    from computer.llm import MiniLM
    lm = MiniLM(endpoint, timeout=60)
    out = []
    for ex in examples:
        out.append(ex)
        if ex["split"] != "train":
            continue
        names = [s.lower().replace("_", " ") for s in ex["subjects"][:1]]
        for _ in range(per_example):
            try:
                q = lm.chat([{"role": "user", "content": "Rewrite this question the way a curious child or adult might "
                              f"ask it out loud. Keep every name. Reply with the question only.\n\n{ex['question']}"}],
                            temperature=0.8, max_tokens=40)
            except Exception as e:  # noqa: BLE001
                print(f"paraphrase failed: {e}", file=sys.stderr)
                return out
            q = q.strip().strip('"').lower().rstrip("?").strip()
            if q and q != ex["question"] and all(n in q for n in names) and len(q) < 160:
                out.append({**ex, "question": q, "kind": ex["kind"], "paraphrased": True})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="data")
    ap.add_argument("--subjects", type=int, default=20000, help="how many subjects to sample")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--year", type=int, default=0, help="answer as of this year (default: now)")
    ap.add_argument("--lexical", type=float, default=1.0,
                    help="share of subjects that also get dictionary questions (meaning, other words, opposite, "
                         "kind of, kinds, related words); 0 = encyclopedic questions only")
    ap.add_argument("--paraphrase", metavar="ENDPOINT", help="OpenAI-compatible server for paraphrases")
    ap.add_argument("--format", choices=["plan", "propose", "direct"], default="plan",
                    help="what the model learns to write: the plan; the plan + its answer (SAY:); "
                         "or the answer alone (knowledge distilled into the weights)")
    ap.add_argument("--no-truths", action="store_true", help="no FALSE / and / or / not examples")
    ap.add_argument("--out", default="navdata")
    a = ap.parse_args(argv)

    corpus = Corpus(a.corpus).load()
    if not corpus.ready:
        sys.exit(f"no corpus: {corpus.error}")
    print(f"corpus: {corpus.status()}")
    ex = generate(corpus, a.subjects, a.seed, a.year,
                  progress=lambda i, n: print(f"  {i} subjects, {n} examples", end="\r", file=sys.stderr),
                  lexical=a.lexical, truths=not a.no_truths)
    ex, leaked = leak_free(ex)
    print(f"\n  leak-free split: dropped {leaked} training examples that mention a held-out thing")
    if a.paraphrase:
        ex = paraphrase(ex, a.paraphrase)
        if a.format in ("propose", "direct"):
            n = paraphrase_answers(ex, a.paraphrase, corpus)
            print(f"  {n} answers re-worded by the model and passed the check")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "examples.jsonl", "w") as fh:
        for e in ex:
            fh.write(json.dumps(e) + "\n")
    for split in ("train", "dev"):
        with open(out / f"{split}.jsonl", "w") as fh:
            for e in ex:
                if e["split"] == split:
                    fh.write(json.dumps(as_chat(e, a.format)) + "\n")
    with open(out / "test.jsonl", "w") as fh:
        for e in ex:
            if e["split"] == "test":
                fh.write(json.dumps(e) + "\n")
    print(f"\n{len(ex)} examples -> {out}/")
    print("  by kind: ", dict(Counter(e["kind"] for e in ex)))
    print("  by split:", dict(Counter(e["split"] for e in ex)))


if __name__ == "__main__":
    main()
