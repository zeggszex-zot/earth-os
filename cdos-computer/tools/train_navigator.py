#!/usr/bin/env python3
"""Phase 4: fine-tune a small pretrained model to write navigator plans.

Run this on a laptop or desktop (a GPU helps, CPU works for 135M), not on the Pi:

    pip install torch transformers
    python3 tools/make_navigator_data.py --corpus data --subjects 20000 --out navdata
    python3 tools/train_navigator.py --data navdata --base HuggingFaceTB/SmolLM2-135M-Instruct --out navigator-135m

Only the reply is learned (the question's tokens are masked out of the loss), in the same chat
shape the computer sends at run time, so no few-shot prompt is needed afterwards (navigator_fewshot = false).
The --format given to make_navigator_data.py decides what the model learns to write: a plan ("lm"),
a plan and its answer ("propose"), or the answer alone ("direct").

Then make it a llama.cpp model for the Pi (or for a server the Pi points at):

    python3 llama.cpp/convert_hf_to_gguf.py navigator-135m --outfile navigator-135m.gguf
    llama.cpp/build/bin/llama-quantize navigator-135m.gguf navigator-135m-q8_0.gguf Q8_0
    llama-server -m navigator-135m-q8_0.gguf --port 8081 -c 512

and in config.toml:

    navigator_planner = "lm"
    navigator_endpoint = "http://127.0.0.1:8081"

Q8_0 rather than Q4: plans are short and exact, and a 135M model at Q8 is about 145 MB.
Measure it with tools/eval_navigator.py --lm before trusting it; the rules stay as the fallback.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FALLBACK_TEMPLATE = ("{% for m in messages %}<|{{ m['role'] }}|>\n{{ m['content'] }}\n{% endfor %}"
                     "{% if add_generation_prompt %}<|assistant|>\n{% endif %}")


def load_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path) if l.strip()]


def encode(tok, row: dict, max_len: int) -> dict | None:
    msgs = row["messages"]
    prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
    target = msgs[-1]["content"].rstrip("\n") + "\n"
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    t_ids = tok(target, add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
    ids = p_ids + t_ids
    if len(ids) > max_len:
        return None
    return {"input_ids": ids, "labels": [-100] * len(p_ids) + t_ids}


def batches(items, size, pad_id, shuffle=True, rng=random):
    import torch
    order = list(range(len(items)))
    if shuffle:
        rng.shuffle(order)
    for i in range(0, len(order), size):
        chunk = [items[j] for j in order[i:i + size]]
        n = max(len(c["input_ids"]) for c in chunk)
        ids = torch.full((len(chunk), n), pad_id, dtype=torch.long)
        lab = torch.full((len(chunk), n), -100, dtype=torch.long)
        att = torch.zeros((len(chunk), n), dtype=torch.long)
        for k, c in enumerate(chunk):
            L = len(c["input_ids"])
            ids[k, :L] = torch.tensor(c["input_ids"])
            lab[k, :L] = torch.tensor(c["labels"])
            att[k, :L] = 1
        yield ids, att, lab


def generate_plan(model, tok, question: str, device, max_new_tokens=160, system: str = "") -> str:
    import torch
    from computer.planner import LM_SYSTEM
    msgs = [{"role": "system", "content": system or LM_SYSTEM}, {"role": "user", "content": f"Q: {question}\n"}]
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    ids = tok(prompt, return_tensors="pt", add_special_tokens=False).to(device)
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id)
    return tok.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True)


def plan_scores(model, tok, rows, device, n=100) -> dict:
    """Valid-output rate and exact-match rate on held-out rows (greedy).  Works for all three
    formats: a plan; a plan + SAY line; a SAY line alone."""
    from computer.plan import PlanError, parse
    from computer.proposer import parse_proposal
    valid = exact = 0
    rows = rows[:n]
    for r in rows:
        q = r["messages"][1]["content"].removeprefix("Q: ").strip()
        want = r["messages"][2]["content"].strip()
        got = generate_plan(model, tok, q, device, system=r["messages"][0]["content"]).strip()
        if "SAY:" in want:
            got = got.split("\nQ:")[0].strip()
            prop = parse_proposal(got)
            valid += bool(prop and prop.say and not prop.plan_error and (prop.plan or not want.startswith("$")))
            exact += got.splitlines()[:len(want.splitlines())] == want.splitlines()
            continue
        if "ANSWER" in got:
            lines = got.splitlines()
            got = "\n".join(lines[: next(i for i, l in enumerate(lines) if "ANSWER" in l) + 1])
        try:
            parse(got)
            valid += 1
        except PlanError:
            pass
        exact += got == want
    return {"valid": valid / max(1, len(rows)), "exact": exact / max(1, len(rows)), "n": len(rows)}


def train(model, tok, train_rows, dev_rows, out: Path, epochs=3, lr=1e-4, batch=16, max_len=384, seed=0,
          device=None, log=print, eval_n=100):
    import torch
    rng = random.Random(seed)
    torch.manual_seed(seed)
    device = device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    model.to(device)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    tr = [e for e in (encode(tok, r, max_len) for r in train_rows) if e]
    dv = [e for e in (encode(tok, r, max_len) for r in dev_rows) if e]
    log(f"{len(tr)} train / {len(dv)} dev rows on {device}")
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps = epochs * math.ceil(len(tr) / batch)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, steps // 20)) *
                                              max(0.0, 1 - s / max(1, steps)))
    step, t0 = 0, time.time()
    for ep in range(epochs):
        model.train()
        for ids, att, lab in batches(tr, batch, tok.pad_token_id, rng=rng):
            loss = model(input_ids=ids.to(device), attention_mask=att.to(device), labels=lab.to(device)).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            step += 1
            if step % 50 == 0 or step == 1:
                log(f"  epoch {ep + 1} step {step}/{steps} loss {loss.item():.4f} ({time.time() - t0:.0f}s)")
        model.eval()
        with torch.no_grad():
            dl = [model(input_ids=i.to(device), attention_mask=a.to(device), labels=l.to(device)).loss.item()
                  for i, a, l in batches(dv, batch, tok.pad_token_id, shuffle=False)] if dv else [float("nan")]
        log(f"epoch {ep + 1}: dev loss {sum(dl) / len(dl):.4f}")
    scores = plan_scores(model, tok, dev_rows, device, n=eval_n) if dev_rows else {}
    log(f"dev plans: {scores}")
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    tok.save_pretrained(out)
    (out / "navigator_train.json").write_text(json.dumps({"epochs": epochs, "lr": lr, "train_rows": len(tr),
                                                          "dev": scores}, indent=2))
    return scores


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="navdata")
    ap.add_argument("--base", default="HuggingFaceTB/SmolLM2-135M-Instruct")
    ap.add_argument("--out", default="navigator-135m")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=384)
    ap.add_argument("--eval-n", type=int, default=200, help="dev questions to generate plans for at the end")
    a = ap.parse_args(argv)

    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.base)
    if not tok.chat_template:
        tok.chat_template = FALLBACK_TEMPLATE
    model = AutoModelForCausalLM.from_pretrained(a.base)
    data = Path(a.data)
    train(model, tok, load_rows(data / "train.jsonl"), load_rows(data / "dev.jsonl"), Path(a.out),
          epochs=a.epochs, lr=a.lr, batch=a.batch, max_len=a.max_len, eval_n=a.eval_n)
    print(f"\nsaved to {a.out}.  Next: convert to GGUF (see the top of this file) and run tools/eval_navigator.py --lm")


if __name__ == "__main__":
    main()
