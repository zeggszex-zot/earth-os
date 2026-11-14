"""
Universal Translator - Convert natural language to shell commands.
The heart of CDOS: translates human speech/text into computer commands.

Design rules (learned the hard way):
  1. Patterns are matched with re.fullmatch, never re.search. A pattern that
     can match a fragment will fire on unrelated sentences -- e.g. a pattern
     of all-optional groups once matched the "i" inside "time".
  2. No pattern may consist entirely of optional groups.
  3. Matching is case-insensitive, but captured groups come from the ORIGINAL
     text so filenames and paths keep their capitalisation.
  4. Order is specific -> general. The first full match wins.
"""

import re
import shlex
import shutil
from typing import Callable, List, Optional, Tuple, Union

# Spoken shorthand -> real filesystem paths
FOLDER_ALIASES = {
    "home": "~",
    "my home": "~",
    "home directory": "~",
    "home folder": "~",
    "downloads": "~/Downloads",
    "download": "~/Downloads",
    "my downloads": "~/Downloads",
    "downloads folder": "~/Downloads",
    "documents": "~/Documents",
    "my documents": "~/Documents",
    "documents folder": "~/Documents",
    "desktop": "~/Desktop",
    "my desktop": "~/Desktop",
    "pictures": "~/Pictures",
    "photos": "~/Pictures",
    "music": "~/Music",
    "videos": "~/Videos",
    "root": "/",
    "the root": "/",
    "temp": "/tmp",
    "tmp": "/tmp",
}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1, "once": 1, "twice": 2,
}


def _clean(raw: Optional[str], strip_articles: bool = True) -> str:
    """
    Trim a captured group and drop trailing filler/punctuation.

    strip_articles is right for locations ("the downloads folder") but wrong
    for names the user chose ("a folder called My Projects" must keep "My").
    """
    if not raw:
        return ""
    text = raw.strip().strip("?.!,")
    if strip_articles:
        text = re.sub(r"^(?:the|my|a|an)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:please|now|for me)$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _path(raw: Optional[str], default: str = ".") -> str:
    """Resolve a spoken path fragment into something a shell understands."""
    text = _clean(raw)
    if not text:
        return default

    alias = FOLDER_ALIASES.get(text.lower())
    if alias:
        return alias

    # "the downloads folder" / "my downloads directory"
    stripped = re.sub(r"\s+(?:folder|directory|dir)$", "", text, flags=re.IGNORECASE).strip()
    alias = FOLDER_ALIASES.get(stripped.lower())
    if alias:
        return alias

    # Already a real path or a literal name -- quote it unless the user
    # clearly typed shell syntax themselves (~, /, *, $).
    if re.search(r"[~/*$]", text):
        return text
    return shlex.quote(text)


def _quote(raw: Optional[str], default: str = "") -> str:
    """Quote a filename captured from speech, preserving the user's wording."""
    text = _clean(raw, strip_articles=False)
    if not text:
        return default
    if re.search(r"[~/*$]", text):
        return text
    return shlex.quote(text)


def _count_of(raw: Optional[str], default: int = 1) -> int:
    text = _clean(raw).lower()
    if not text:
        return default
    if text.isdigit():
        return max(1, int(text))
    return WORD_NUMBERS.get(text, default)


class CommandTranslator:
    """Translate natural language into shell commands."""

    def __init__(self):
        # (regex, handler_or_command, human-readable example)
        self.patterns: List[Tuple[str, Union[str, Callable], str]] = []
        #: shutil.which is a filesystem hit; the answer does not change
        #: within a session, so it is remembered.
        self._which_cache: dict = {}
        self._build_patterns()

    # ------------------------------------------------------------------
    # Pattern table
    # ------------------------------------------------------------------
    def _build_patterns(self):
        P = self.patterns.append

        # --- Location ---------------------------------------------------
        P((r"(?:where\s+am\s+i|what\s+(?:directory|folder)\s+am\s+i\s+in"
           r"|what(?:'|’)?s?\s+my\s+(?:current\s+)?(?:directory|folder|location)"
           r"|current\s+(?:directory|folder)|show\s+me\s+where\s+i\s+am)",
           "pwd", "where am I"))

        # --- Navigation -------------------------------------------------
        P((r"(?:go|take\s+me|navigate|cd)\s+(?:back\s+)?home",
           "cd ~", "go home"))

        P((r"(?:go|move|navigate|step)\s+(?:back|up)\s+(\w+)\s+(?:directories|directory|folders|folder|levels|level|times)",
           self._go_back, "go back two directories"))

        P((r"(?:go|move|navigate|step)\s+(?:back|up)(?:\s+(?:one|a)\s+(?:level|directory|folder))?",
           "cd ..", "go up"))

        P((r"(?:go|change|move|navigate|switch|cd)\s+(?:to|into|in)\s+(.+)",
           self._change_dir, "go to downloads"))

        P((r"open\s+(?:the\s+)?(.+?)\s+(?:folder|directory)",
           self._change_dir, "open the downloads folder"))

        # --- Listing ----------------------------------------------------
        P((r"(?:show|list|display)\s+(?:me\s+)?(?:all\s+)?(?:the\s+|my\s+)?"
           r"(?:files|contents|everything)\s+(?:in|inside|of|from)\s+(.+)",
           self._list_files, "show me the files in downloads"))

        P((r"(?:show|list|display)\s+(?:me\s+)?(?:all\s+)?(?:the\s+|my\s+)?"
           r"(?:files|contents|everything)",
           "ls -la", "show me the files"))

        P((r"what(?:'|’)?s?\s+(?:in|inside)\s+(?:this|the\s+current)\s+(?:folder|directory)",
           "ls -la", "what's in this folder"))

        P((r"what(?:'|’)?s?\s+(?:in|inside)\s+(.+)",
           self._list_files, "what's in downloads"))

        P((r"(?:what|which)\s+files\s+(?:are\s+)?(?:here|in\s+here|in\s+this\s+(?:folder|directory))",
           "ls -la", "what files are here"))

        # --- File creation / removal ------------------------------------
        P((r"(?:create|make|new|add)\s+(?:a\s+|an\s+)?(?:new\s+)?"
           r"(?:folder|directory|dir)\s+(?:called|named)\s+(.+)",
           self._make_dir, "create a folder called projects"))

        P((r"(?:create|make|new|add)\s+(?:a\s+|an\s+)?(?:new\s+)?"
           r"(?:file|document)\s+(?:called|named)\s+(.+)",
           self._create_file, "create a file called notes.txt"))

        P((r"(?:delete|remove|erase|trash)\s+(?:the\s+)?(?:folder|directory)\s+(.+)",
           self._delete_dir, "delete the folder oldstuff"))

        P((r"(?:delete|remove|erase|trash)\s+(?:the\s+)?(?:file\s+)?(.+)",
           self._delete_file, "delete the file notes.txt"))

        # --- Reading / copying ------------------------------------------
        P((r"(?:read|open|cat|view)\s+(?:the\s+)?(?:file\s+)?(.+)",
           self._read_file, "read notes.txt"))

        P((r"(?:show|display)\s+(?:me\s+)?(?:the\s+)?contents?\s+of\s+(.+)",
           self._read_file, "show me the contents of notes.txt"))

        P((r"(?:copy|duplicate)\s+(.+?)\s+(?:to|into)\s+(.+)",
           self._copy, "copy notes.txt to backup.txt"))

        P((r"(?:move|rename)\s+(.+?)\s+(?:to|into)\s+(.+)",
           self._move, "rename notes.txt to todo.txt"))

        # --- Time / identity --------------------------------------------
        P((r"(?:what\s+)?time\s+is\s+it|what(?:'|’)?s?\s+the\s+time|(?:show|tell)\s+me\s+the\s+time",
           "date '+%-I:%M %p'", "what time is it"))

        P((r"what(?:'|’)?s?\s+(?:today(?:'|’)?s?\s+)?(?:the\s+)?date"
           r"|what\s+day\s+is\s+it|what(?:'|’)?s?\s+today",
           "date '+%A, %B %-d, %Y'", "what's today's date"))

        P((r"who\s+am\s+i|what(?:'|’)?s?\s+my\s+(?:username|user\s*name|login)"
           r"|(?:show|tell)\s+me\s+my\s+username",
           "whoami", "who am I"))

        # --- System -----------------------------------------------------
        P((r"(?:show|tell|give)\s+me\s+(?:the\s+)?system\s+(?:info|information|details|specs)"
           r"|system\s+(?:info|information|details|specs)"
           r"|what\s+system\s+am\s+i\s+(?:running|on)",
           "uname -a", "show me system info"))

        P((r"(?:how\s+much\s+)?(?:disk|storage)\s+(?:space|usage)(?:\s+(?:do\s+i\s+have|is\s+left|left))?"
           r"|how\s+much\s+(?:disk\s+)?space\s+(?:do\s+i\s+have|is\s+left|is\s+there)",
           "df -h", "how much disk space is left"))

        P((r"(?:how\s+much\s+)?(?:memory|ram)(?:\s+(?:usage|info|is\s+(?:used|free|left)|do\s+i\s+have))?",
           "free -h", "how much memory"))

        P((r"(?:show|list)\s+(?:me\s+)?(?:the\s+)?(?:running\s+)?(?:processes|tasks|programs)"
           r"|what(?:'|’)?s?\s+running|what\s+processes\s+are\s+running",
           "ps aux --sort=-%cpu | head -20", "what's running"))

        P((r"(?:what(?:'|’)?s?\s+(?:the\s+)?)?uptime|how\s+long\s+has\s+(?:this|it|the\s+computer)\s+been\s+(?:on|up|running)",
           "uptime", "uptime"))

        P((r"what(?:'|’)?s?\s+my\s+ip(?:\s+address)?|show\s+me\s+my\s+ip(?:\s+address)?",
           "hostname -I", "what's my IP"))

        P((r"(?:check\s+(?:the\s+)?)?(?:network|internet|connection)"
           r"|am\s+i\s+(?:online|connected)|ping\s+(?:the\s+)?(?:internet|network)",
           "ping -c 4 8.8.8.8", "am I online"))

        # --- Search / text ----------------------------------------------
        # "grep" is deliberately absent as a leading verb here. It is a real
        # command, so someone who types it means it -- treating it as English
        # turned "grep -rn TODO ." into a search for the literal "-rn TODO".
        P((r"(?:search|look)\s+(?:for\s+)?(.+?)\s+(?:in|inside|within)\s+(.+)",
           self._grep, "search for error in log.txt"))

        P((r"find\s+(?:a\s+)?(?:file|files)\s+(?:called|named)\s+(.+)",
           self._find_file, "find a file called notes.txt"))

        P((r"search\s+(?:for\s+)?(.+)",
           self._grep_here, "search for TODO"))

        P((r"(?:count|how\s+many)\s+(?:the\s+)?lines?\s+(?:in|of|from)\s+(.+)",
           self._count_lines, "count the lines in data.csv"))

        P((r"(?:show|display)\s+(?:me\s+)?(?:the\s+)?first\s+(\w+)\s+lines?\s+(?:of|in|from)\s+(.+)",
           self._head, "show me the first 20 lines of log.txt"))

        P((r"(?:show|display)\s+(?:me\s+)?(?:the\s+)?last\s+(\w+)\s+lines?\s+(?:of|in|from)\s+(.+)",
           self._tail, "show the last 20 lines of log.txt"))

        # --- Misc -------------------------------------------------------
        P((r"(?:clear|clean|empty|wipe)\s+(?:the\s+)?(?:screen|terminal|display)|clear",
           "clear", "clear the screen"))

        P((r"(?:what\s+can\s+i\s+do|what\s+can\s+you\s+do|show\s+me\s+(?:the\s+)?commands"
           r"|list\s+(?:the\s+)?commands)",
           "help", "what can you do"))

        P((r"(?:calculate|compute|work\s+out|what\s+is)\s+([\d\s\+\-\*/\.\(\)%]+)",
           self._calculate, "calculate 12 * 7"))

        P((r"(?:what\s+)?(?:version\s+of\s+)?python(?:\s+version)?(?:\s+am\s+i\s+running)?",
           "python3 --version", "what python version"))

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------
    def translate(self, natural_text: str) -> str:
        """
        Translate natural language into a shell command.
        Returns the original text unchanged when nothing matches, so the
        caller can fall through to running it as a plain shell command.
        """
        if not natural_text:
            return natural_text

        # Normalise whitespace and strip trailing punctuation, but keep case.
        text = re.sub(r"\s+", " ", natural_text.strip())
        text = text.rstrip("?!.")
        text = re.sub(r"^(?:please|hey|ok|okay|cdos|computer)[,\s]+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*,?\s*please$", "", text, flags=re.IGNORECASE)

        if not text:
            return natural_text

        # If it already is a command, leave it alone. Someone who types
        # "grep -rn TODO ." or "tar -xzf x.tgz" means exactly that, and
        # no amount of English pattern matching should second-guess it.
        if self._looks_like_a_command(text):
            return natural_text

        for pattern, handler, _example in self.patterns:
            match = re.fullmatch(pattern, text, re.IGNORECASE)
            if match:
                result = handler(match) if callable(handler) else handler
                if result:
                    return result

        return natural_text

    # ------------------------------------------------------------------
    def _looks_like_a_command(self, text: str) -> str:
        """Whether this is already a shell command rather than a sentence.

        The test is deliberately narrow: the first word has to be a real
        executable on this machine AND the line has to carry something no
        English sentence would -- an option flag, a pipe, a redirect. Plain
        "find a file called notes.txt" still reads as English even though
        ``find`` is a program.
        """
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()
        if not tokens:
            return False

        if any(c in text for c in "|><&;$`"):
            return True

        has_flag = any(t.startswith("-") and len(t) > 1 for t in tokens[1:])
        if not has_flag:
            return False

        return self._is_executable(tokens[0])

    def _is_executable(self, word: str) -> bool:
        if word in self._which_cache:
            return self._which_cache[word]
        found = shutil.which(word) is not None
        self._which_cache[word] = found
        return found

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _list_files(self, m) -> str:
        return f"ls -la {_path(m.group(1))}"

    def _change_dir(self, m) -> str:
        return f"cd {_path(m.group(1), '~')}"

    def _go_back(self, m) -> str:
        return "cd " + "../" * _count_of(m.group(1))

    def _create_file(self, m) -> str:
        name = _quote(m.group(1))
        return f"touch {name}" if name else ""

    def _make_dir(self, m) -> str:
        name = _quote(m.group(1))
        return f"mkdir -p {name}" if name else ""

    def _delete_file(self, m) -> str:
        name = _quote(m.group(1))
        # -i so a misheard filename asks before destroying anything.
        return f"rm -i {name}" if name else ""

    def _delete_dir(self, m) -> str:
        name = _quote(m.group(1))
        return f"rm -ri {name}" if name else ""

    def _read_file(self, m) -> str:
        name = _quote(m.group(1))
        return f"cat {name}" if name else ""

    def _copy(self, m) -> str:
        src, dst = _quote(m.group(1)), _path(m.group(2), "")
        return f"cp -r {src} {dst}" if src and dst else ""

    def _move(self, m) -> str:
        src, dst = _quote(m.group(1)), _path(m.group(2), "")
        return f"mv {src} {dst}" if src and dst else ""

    def _grep(self, m) -> str:
        needle = _clean(m.group(1))
        target = _path(m.group(2))
        if not needle:
            return ""
        return f"grep -rn -- {shlex.quote(needle)} {target}"

    def _grep_here(self, m) -> str:
        needle = _clean(m.group(1))
        if not needle:
            return ""
        return f"grep -rn -- {shlex.quote(needle)} ."

    def _find_file(self, m) -> str:
        name = _clean(m.group(1))
        return f"find . -iname {shlex.quote(name)}" if name else ""

    def _count_lines(self, m) -> str:
        name = _quote(m.group(1))
        return f"wc -l {name}" if name else ""

    def _head(self, m) -> str:
        return f"head -n {_count_of(m.group(1), 10)} {_quote(m.group(2))}"

    def _tail(self, m) -> str:
        return f"tail -n {_count_of(m.group(1), 10)} {_quote(m.group(2))}"

    def _calculate(self, m) -> str:
        expr = _clean(m.group(1))
        if not expr or not re.fullmatch(r"[\d\s\+\-\*/\.\(\)%]+", expr):
            return ""
        return "python3 -c " + shlex.quote(f"print({expr})")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def get_pattern_count(self) -> int:
        return len(self.patterns)

    def list_patterns(self) -> str:
        lines = ["Universal Translator - recognised phrases:", ""]
        for i, (_pattern, handler, example) in enumerate(self.patterns, 1):
            result = self.translate(example)
            lines.append(f"{i:2d}. \"{example}\"")
            lines.append(f"     -> {result}")
        return "\n".join(lines)


if __name__ == "__main__":
    t = CommandTranslator()
    print(t.list_patterns())
    print(f"\nTotal patterns: {t.get_pattern_count()}")
