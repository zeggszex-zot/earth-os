"""How many arguments a macro takes, and which are optional: read from the document's own preamble first.

A profile (profiles/*.json) says what a macro *means* (\\Chapter is a chapter heading, \\Graphic is an inline
illustration) and gives a fallback argument pattern.  The pattern actually used is the one the document defines,
when the preamble defines it in a form that can be read without running TeX:

    \\newcommand{\\Chapter}[3][]{...}                 -> o m m
    \\NewDocumentCommand{\\Chapter}{o m m}{...}        -> o m m
    \\def\\@Chapter[#1]#2#3{...}                       -> o m m
    \\newcommand{\\Chapter}{\\@ifnextchar[\\@Chapter\\@@Chapter}   -> the [ branch's pattern (here @Chapter's)

A pattern the preamble does not settle (a \\def with other delimiters, a command defined with no arguments that
dispatches some other way) falls back to the profile, and the parsing report says so for every structural macro.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import scanner as sc

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"

# Standard LaTeX that every profile starts from.  Starred forms (\section*) are handled by the parser.
BASE_MACROS = {
    "part":          {"role": "heading", "kind": "part", "level": 0, "args": "om"},
    "chapter":       {"role": "heading", "kind": "chapter", "level": 1, "args": "om"},
    "section":       {"role": "heading", "kind": "section", "level": 2, "args": "om"},
    "subsection":    {"role": "heading", "kind": "subsection", "level": 3, "args": "om"},
    "subsubsection": {"role": "heading", "kind": "subsubsection", "level": 4, "args": "om"},
    "paragraph":     {"role": "heading", "kind": "paragraph", "level": 5, "args": "om"},
    "includegraphics": {"role": "figure", "block": False, "args": "om"},
    "caption":       {"role": "caption", "args": "om"},
    "tag":           {"role": "equation_tag", "args": "m"},
    "label":         {"role": "label", "args": "m"},
    "title":         {"role": "metadata", "field": "title", "args": "om"},
    "author":        {"role": "metadata", "field": "author", "args": "m"},
    "date":          {"role": "metadata", "field": "date", "args": "m"},
}
BASE_ENVS = {
    "math": ["equation", "equation*", "align", "align*", "alignat", "alignat*", "gather", "gather*",
             "multline", "multline*", "flalign", "flalign*", "eqnarray", "eqnarray*", "displaymath", "math"],
    "verbatim": ["verbatim", "verbatim*", "Verbatim", "lstlisting", "comment"],
    "figure": ["figure", "figure*", "wrapfigure"],
    "table": ["tabular", "tabular*", "tabularx", "longtable"],
    "list": ["itemize", "enumerate", "description"],
}
DEFINERS = {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"}
DOC_DEFINERS = {"NewDocumentCommand", "RenewDocumentCommand", "DeclareDocumentCommand", "ProvideDocumentCommand"}
DISPATCH = ("@ifnextchar", "kernel@ifnextchar", "@ifstar", "@dblarg", "IfValueTF", "@testopt")


@dataclass
class Signature:
    name: str
    args: str               # "o" optional [..], "m" mandatory {..}; e.g. "omm"
    origin: str             # preamble | profile | base
    line: int | None = None  # where the preamble defines it
    note: str = ""


@dataclass
class Definition:
    name: str
    args: str | None        # None: defined, but in a form whose pattern can't be read without TeX
    line: int
    body: str
    kind: str               # newcommand | xparse | def
    source: str = ""        # the whole definition as written


class Profile:
    """What macros mean for one family of documents.  A profile may extend another ("extends": "pg-latex"):
    its macros, drop list and environments are laid over the parent's, key by key, so a book profile states only
    how the book differs."""

    def __init__(self, data: dict, path: Path | None = None, chain: tuple = ()):
        if data.get("format") != "edie-profile/1":
            raise ValueError(f"{path or 'profile'}: format must be edie-profile/1, not {data.get('format')!r}")
        self.name = data["name"]
        self.path = path
        self.chain = chain + (self.name,)
        self.raw = data
        self.macros = {**BASE_MACROS, **data.get("macros", {})}
        envs = data.get("environments", {})
        self.math_envs = set(BASE_ENVS["math"]) | set(envs.get("math", []))
        self.verbatim_envs = set(BASE_ENVS["verbatim"]) | set(envs.get("verbatim", []))
        self.figure_envs = set(BASE_ENVS["figure"]) | set(envs.get("figure", []))
        self.table_envs = set(BASE_ENVS["table"]) | set(envs.get("table", []))
        self.list_envs = set(BASE_ENVS["list"]) | set(envs.get("list", []))
        self.drop = data.get("drop_macros", {})          # name -> args pattern, removed with its arguments
        self.image_dirs = data.get("image_dirs", ["images", "."])
        self.image_exts = data.get("image_extensions", ["", ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg", ".gif"])
        self.selected = "named"

    @staticmethod
    def _read(p: Path, seen: tuple = ()) -> tuple[dict, tuple]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except OSError as e:
            raise ValueError(f"profile {p.stem!r} not found ({p})") from e
        parent = data.get("extends")
        if not parent:
            return data, (data.get("name"),)
        if parent in seen:
            raise ValueError(f"profile {data.get('name')!r}: 'extends' loops through {parent!r}")
        base, chain = Profile._read(PROFILE_DIR / f"{parent}.json", seen + (data.get("name"),))
        merged = {**base, **{k: v for k, v in data.items() if k not in ("macros", "environments", "drop_macros")}}
        merged["macros"] = {**base.get("macros", {}), **data.get("macros", {})}
        merged["drop_macros"] = {**base.get("drop_macros", {}), **data.get("drop_macros", {})}
        envs = {k: list(v) for k, v in base.get("environments", {}).items()}
        for k, v in data.get("environments", {}).items():
            envs[k] = sorted(set(envs.get(k, [])) | set(v))
        merged["environments"] = envs
        return merged, chain + (data.get("name"),)

    @classmethod
    def load(cls, name_or_path: str) -> "Profile":
        p = Path(name_or_path)
        if not p.suffix:
            p = PROFILE_DIR / f"{name_or_path}.json"
        data, chain = cls._read(p)
        return cls(data, p, chain[:-1])

    @classmethod
    def auto(cls, text: str) -> "Profile":
        """The profile for this document: pg-<ebook number> when one ships with EDIE, else pg-latex.  The ebook
        number is the document's own \\def\\ebook{N}, or failing that its Project Gutenberg header."""
        import re
        m = (re.search(r"^\\def\\ebook\{(\d+)\}", text, re.M)
             or re.search(r"\[E[Bb]ook #(\d+)\]", text[:20000]))
        if m and (PROFILE_DIR / f"pg-{m.group(1)}.json").is_file():
            prof = cls.load(f"pg-{m.group(1)}")
            prof.selected = f"auto: ebook {m.group(1)} has its own profile"
        else:
            prof = cls.load("pg-latex")
            prof.selected = (f"auto: no profile for ebook {m.group(1)}; the generic one" if m
                             else "auto: no ebook number; the generic one")
        return prof

    def describe(self) -> dict:
        import hashlib
        blob = json.dumps(self.raw, sort_keys=True).encode()
        return {"name": self.name, "extends": list(self.chain[:-1]), "selected": self.selected,
                "sha256": hashlib.sha256(blob).hexdigest()}


def _name_after(text: str, i: int) -> tuple[str | None, int]:
    """The macro being defined, written \\Name or {\\Name}, starting at or after i."""
    j = sc.skip_arg_space(text, i)
    if j < len(text) and text[j] == "{":
        end = sc.find_group_end(text, j)
        inner = text[j + 1:end - 1].strip()
        if inner.startswith("\\"):
            name, _ = sc.read_control(inner, 0, at_letter=True)
            return name, end
        return None, end
    if j < len(text) and text[j] == "\\":
        name, end = sc.read_control(text, j, at_letter=True)
        return name, end
    return None, j


def _def_params(params: str) -> str | None:
    """\\def parameter text to a pattern: [#1]#2#3 -> omm.  Anything else is not readable without TeX."""
    out, k = "", 0
    params = params.strip()
    while k < len(params):
        if params.startswith("[#", k) and k + 3 < len(params) and params[k + 2].isdigit() and params[k + 3] == "]":
            out += "o"
            k += 4
        elif params[k] == "#" and k + 1 < len(params) and params[k + 1].isdigit():
            out += "m"
            k += 2
        elif params[k] in " \t\n":
            k += 1
        else:
            return None
    return out


def _xparse(spec: str) -> str | None:
    out, k = "", 0
    while k < len(spec):
        c = spec[k]
        if c in " \t\n":
            k += 1
        elif c == "m":
            out, k = out + "m", k + 1
        elif c == "o":
            out, k = out + "o", k + 1
        elif c == "O":
            k += 1
            j = sc.skip_arg_space(spec, k)
            if j < len(spec) and spec[j] == "{":
                k = sc.find_group_end(spec, j)
            out += "o"
        elif c == "s":
            k += 1                               # a star is read by the parser, not counted as an argument
        else:
            return None
    return out


def scan_preamble(text: str, end: int, line_of) -> dict:
    """Every macro definition in text[:end] that names a macro, keyed by name (a later definition replaces an
    earlier one, as in TeX)."""
    defs: dict = {}
    i = 0
    while i < end:
        ch = text[i]
        if ch == "%":
            i = sc.skip_comment(text, i)
            continue
        if ch != "\\":
            i += 1
            continue
        word, j = sc.read_control(text, i, at_letter=True)
        try:
            if word in DEFINERS:
                if j < end and text[j] == "*":
                    j += 1
                name, j = _name_after(text, j)
                n = sc.read_optional(text, j)
                nargs, has_default = 0, False
                if n:
                    j = n.end
                    try:
                        nargs = int(n.inner.strip())
                    except ValueError:
                        nargs = -1
                    d = sc.read_optional(text, j)
                    if d:
                        has_default, j = True, d.end
                body = sc.read_group(text, j)
                if body:
                    j = body.end
                if name:
                    args = None if nargs < 0 else (("o" + "m" * (nargs - 1)) if has_default else "m" * nargs)
                    defs[name] = Definition(name, args, line_of(i), body.inner if body else "", "newcommand",
                                            text[i:j])
            elif word in DOC_DEFINERS:
                name, j = _name_after(text, j)
                spec = sc.read_group(text, j)
                body = sc.read_group(text, spec.end) if spec else None
                if name and spec:
                    j = body.end if body else spec.end
                    defs[name] = Definition(name, _xparse(spec.inner), line_of(i), body.inner if body else "", "xparse",
                                            text[i:j])
            elif word in ("def", "gdef", "edef", "xdef"):
                k = sc.skip_arg_space(text, j)
                if k < end and text[k] == "\\":
                    name, k = sc.read_control(text, k, at_letter=True)
                    b = text.find("{", k)
                    if b >= 0:
                        params = text[k:b]
                        body_end = sc.find_group_end(text, b)
                        defs[name] = Definition(name, _def_params(params), line_of(i), text[b + 1:body_end - 1], "def",
                                                text[i:body_end])
                        j = body_end
        except sc.ScanError:
            pass                                 # an unreadable definition is simply not used
        i = max(j, i + 1)
    return defs


def _dispatch_target(body: str) -> tuple[str | None, str | None]:
    """For a body like \\@ifnextchar[{\\@Chapter}{\\@@Chapter}: the macro taken when [ follows, and the other one."""
    for d in DISPATCH[:2]:
        k = body.find("\\" + d)
        if k < 0:
            continue
        rest = body[k + 1 + len(d):].lstrip()
        if not rest.startswith("["):
            continue
        rest = rest[1:]
        names = []
        p = 0
        while len(names) < 2 and p < len(rest):
            q = sc.skip_arg_space(rest, p)
            if q >= len(rest):
                break
            if rest[q] == "{":
                e = sc.find_group_end(rest, q)
                inner = rest[q + 1:e - 1].strip()
                names.append(sc.read_control(inner, 0, at_letter=True)[0] if inner.startswith("\\") else None)
                p = e
            elif rest[q] == "\\":
                nm, p = sc.read_control(rest, q, at_letter=True)
                names.append(nm)
            else:
                break
        return (names + [None, None])[0], (names + [None, None])[1]
    return None, None


def resolve(profile: Profile, defs: dict) -> tuple[dict, list]:
    """The signature for every macro the profile gives a role, plus any the preamble defines readably.
    Returns (signatures by name, notes for the report)."""
    sigs: dict = {}
    notes: list = []
    for name, d in defs.items():
        args, note = d.args, ""
        if args == "" and any("\\" + x in d.body for x in DISPATCH):
            yes, no = _dispatch_target(d.body)
            ya = defs[yes].args if yes in defs else None
            na = defs[no].args if no in defs else None
            if ya is not None:
                args = ya if ya.startswith("o") else "o" + ya
                note = f"dispatches on [ to \\{yes}"
                if na is not None and "o" + na != args:
                    note += f"; without [ \\{no} takes {na or 'nothing'}"
            else:
                args, note = None, "dispatches on what follows; pattern not readable"
        if args is not None:
            sigs[name] = Signature(name, args, "preamble", d.line, note)
        elif name in profile.macros:
            notes.append({"macro": name, "line": d.line, "note": note or f"{d.kind} definition not readable"})
    for name, spec in profile.macros.items():
        want = spec.get("args", "")
        origin = "base" if name in BASE_MACROS and name not in profile.raw.get("macros", {}) else "profile"
        if name in sigs:
            if sigs[name].args != want and origin == "profile":
                msg = (f"preamble defines {sigs[name].args or 'no arguments'}; profile expected "
                       f"{want or 'no arguments'}; the preamble is used")
                sigs[name].note = "; ".join(x for x in (sigs[name].note, msg) if x)
                if spec.get("verified"):        # a pattern someone checked against the source: worth a warning
                    notes.append({"macro": name, "line": sigs[name].line, "code": "SIGNATURE_MISMATCH", "note": msg})
            continue
        sigs[name] = Signature(name, want, origin, None,
                               "" if origin == "base" else "not readable from the preamble; profile pattern used")
    return sigs, notes


def as_records(sigs: dict, profile: Profile) -> list:
    """For the document: one record per macro that has a role, sorted by name."""
    return [asdict(sigs[n]) | {"role": profile.macros[n]["role"]} for n in sorted(profile.macros) if n in sigs]
