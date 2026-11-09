# Porting notes: LCARS x32 (2010) → EARTH OS

What the original was, what could cross to Linux, and what had to be rebuilt.

## What was in the archive

`Incremental_Build_09_09_2010.zip` is a **binary distribution** of LCARS x32,
a Windows shell replacement. There is **no source code** in it — only compiled
assemblies and their debug databases.

```
LCARSmain.exe          1.1 MB   VB.NET / WinForms, x86    the shell itself
LCARS.dll              120 KB   VB.NET                    the widget toolkit
LCARSsettings.exe      344 KB                             SPECIFY PARAMETERS
LCARSdestruct.exe      192 KB                             auto-destruct timer
LCARSexplorer.exe      102 KB                             file browser
OnScreenKeyboard.exe   176 KB                             touch keyboard
LCARSengineering.exe    65 KB                             status panel
LCARSpic.exe            82 KB                             photo viewer
LCARSshutdown.exe       49 KB                             log off / shut down
LCARSlauncher.exe       29 KB                             process launcher
SetShell.exe            33 KB                             registry shell swap
Interop.SpeechLib.dll  163 KB   COM interop               SAPI 5 speech
Interop.WMPLib.dll     274 KB   COM interop               Windows Media Player
AxInterop.WMPLib.dll    49 KB   COM interop               WMP ActiveX host
Interop.IWshRuntime...  49 KB   COM interop               Windows Script Host
bass.dll               100 KB   native x86                BASS audio library
*.pdb                           debug symbols
```

Reading the .NET metadata out of `LCARS.dll` gives the full control set the
original was built from:

```
StandardButton   HalfPillButton   ComplexButton   TextButton   FlatButton
Elbow            ArrowButton      LevelIndicator  PieButton    ScrollBar
ScrollPanel      x32TabControl    x32TabPage      Listbox      LCARSfileBrowseDialog
```

and `LCARSmain.exe` gives the shell's structure: `frmMainscreen1` through
`frmMainscreen4` (the four layouts behind MODE SELECT), `frmRedAlert`,
`frmStartup`, `frmFirstRun`, `frmManageButtons`, `frmIconSaver`, plus modules
`modSpeech`, `modBusiness`, `modAnimate`, `programList`, `MSIparser`, and Win32
interop structures (`APPBARDATA`, `WINDOWPLACEMENT`, `SHFILEINFO`,
`COPYDATASTRUCT`) — the machinery of being a Windows shell.

## Why "port" meant "reimplement"

Three things made a mechanical port impossible:

1. **No source.** Only IL in compiled assemblies.
2. **WinForms.** Mono's WinForms is effectively unmaintained, and the original
   is a fixed-coordinate design laid out for a 2010 screen. Even a perfect
   translation would give a letterboxed window on a modern display.
3. **Windows-only dependencies that have no Linux equivalent to swap in.**
   SAPI 5 for speech, Windows Media Player ActiveX for audio and video,
   Windows Script Host for `.lnk` handling, and the Win32 shell APIs
   (`SHAppBarMessage`, shell hooks, `SetShell` writing the shell out of the
   registry). Under Wine, `LCARSmain.exe` would either refuse to start or come
   up mute, mouse-only, and unable to act as a shell at all.

So the toolkit, the shell logic and the speech stack are rewritten natively.
What *is* preserved, verbatim, is the data.

## What crossed over unchanged

These files are copied from the archive and read at runtime by the same rules
the original used:

| Asset | Where it lives now | Notes |
|---|---|---|
| `lcars.ttf` | `earth_os/assets/fonts/` | Registered via fontconfig at startup. Declares family `LCARS,Swiss911 UCm BT`. |
| `Standard Colors.lxcp`, `Nemesis.lxcp`, `Old School.lxcp`, `test colors.lxcp` | `earth_os/assets/palettes/` | Parsed by `theme.Palette` |
| `Lang/Standard.lng`, `Lang/TNG.lng` | `earth_os/assets/lang/` | Parsed by `theme.Strings` |
| `vCommands.ini` | `earth_os/assets/` | Parsed by `commands.Vocabulary` |
| `commands.xml` | `earth_os/assets/` | SAPI grammar; the wake word is read from its `Init` rule |
| `beep.wav`, `ack.wav`, `computer.wav`, `redalert.wav` | `earth_os/assets/sounds/` | Played through `audio.SoundPlayer` |

Drop any other `.lxcp` or `.lng` file next to them and it appears in Settings.

### `.lxcp` slot mapping

An `.lxcp` is eleven colours, one per line, hex or a .NET colour name. The
binaries record only the *order* they are read in, not what each slot is for,
so the functional names are this port's reading — recovered by comparing the
four shipped palettes against the original's artwork:

| Line | Name here | Standard Colors | Purpose |
|---|---|---|---|
| 1 | `accent1` | `#3366CC` | dominant structural colour |
| 2 | `accent2` | `#99CCFF` | secondary buttons |
| 3 | `accent3` | `#CC99CC` | tertiary buttons |
| 4 | `accent4` | `#FFCC00` | primary action buttons |
| 5 | `accent5` | `#FFFF99` | highlights and readouts |
| 6 | `accent6` | `#CC6666` | muted secondary |
| 7 | `text` | `#FFFFFF` | body text |
| 8 | `alert` | `#FF0000` | red alert and destruct |
| 9 | `frame` | `#FFCC66` | elbows and framing |
| 10 | `highlight` | `Orange` | hover and active state |
| 11 | `select` | `#99CCFF` | selection and focus |

If you disagree with a slot, override it by name in `config.toml` under
`[colors]` — nothing is hard-coded.

## Control-by-control mapping

| `LCARS.dll` | `earth_os.widgets` | Notes |
|---|---|---|
| `StandardButton` | `StandardButton` | pill, both ends rounded |
| `HalfPillButton` | `HalfPillButton` | pill, one end rounded |
| `FlatButton` | `FlatButton` | rectangle |
| `ComplexButton` | `ComplexButton` | detached cap plus body |
| `TextButton` | `TextButton` | cap, label, rule to the edge |
| `Elbow` | `Elbow` | all four corners, correct inner/outer radii |
| `ArrowButton` | `ArrowButton` | solid triangle or chevron |
| `LevelIndicator` | `LevelIndicator` | segmented bar, either axis |
| `PieButton` | `PieButton` | wedge or annulus |
| `ScrollPanel`, `ScrollBar` | `ScrollPanel` | clipped, callback-drawn content |
| `x32TabControl`, `x32TabPage` | `TabStrip` | tab selector |
| `Listbox` | `ListPanel` | selectable rows |
| `LCARSfileBrowseDialog` | `screens.BrowserScreen` | promoted to a full screen |
| — | `BarRun`, `Meter`, `Label` | added by this port |

Rather than one GTK widget per control — which the original effectively did,
with hundreds of WinForms children per form — every screen is a single
`GtkDrawingArea` holding a retained list of elements, painted with Cairo and
hit-tested by hand. That is what makes full-screen scaling free and keeps the
frame cost flat.

## Screen mapping

| Original | Here | Change |
|---|---|---|
| `frmMainscreen1-4` | `screens/main.py` | One scalable layout instead of four fixed ones. Centre is the computer conversation rather than wallpaper. |
| `frmRedAlert` | `screens/alert.py` | Same idea, pulse driven by the frame clock. |
| `LCARSdestruct.exe` | `screens/destruct.py` | Now requires a second confirmation to arm, and can target poweroff/reboot/logoff/nothing. |
| `LCARSengineering.exe` | `screens/engineering.py` | Reads real `/proc` and `/sys` instead of drawing decorative graphs. |
| `LCARSexplorer.exe` | `screens/browser.py` | Opens files through `xdg-open`. |
| `LCARSsettings.exe` | `screens/settings.py` | Tabs for appearance, voice, audio, diagnostics; writes `config.toml`. |
| Start Menu / `frmManageButtons` | `screens/programs.py` | Walks XDG `.desktop` entries instead of the Windows Start Menu. |
| `LCARSpic.exe` | not ported | Hands images to the system viewer instead. |
| `OnScreenKeyboard.exe` | not ported | Use `squeekboard` or `wvkbd`; see below. |
| `SetShell.exe` | `session/` | A Wayland session entry replaces editing the registry's `Shell` value. |
| `LCARSlauncher.exe` | `core.Shell.launch` | `subprocess` in a new session. |
| `LCARSshutdown.exe` | `core.Shell.shutdown` / `log_off` | `systemctl` / `loginctl`. |

## The speech stack

The original chained SAPI 5 recognition to a fixed XML grammar: it could
recognise the 31 phrases in `commands.xml` and nothing else, and it could not
answer a question — there was no model behind it, only a command table.

This port keeps that table, then puts a model behind it:

```
microphone ─ energy gate ─ WAV ─ speech-to-text ─┬─ matches vCommands? ─ run it
                                                 └─ otherwise ─ reasoning ─ speech
```

- **Recognition** is whisper.cpp by default (`vosk` as an alternative), instead
  of SAPI.
- **Speech** is Piper, falling back to `espeak-ng`, instead of SAPI's voices.
- **Reasoning** is Ollama locally, or a cloud API, where the original had
  nothing.
- **Wake word** is still read from the `Init` rule of `commands.xml`, so it is
  still "computer", and `continuous commands` / `end commands` still toggle
  whether it is required — exactly as `vCommands.ini` documents.

Phrase matching is more forgiving than SAPI's fixed grammar: exact match, then
substring, then a fuzzy pass, so "computer, open my documents please" resolves
to `my documents`.

## Things deliberately not carried across

- **The UFP logo and the original bitmaps.** They are Paramount's, and the
  carved bitmaps are the original author's artwork. The shell draws its own
  furniture instead; point `[shell] background` at your own image if you want
  one.
- **`bass.dll` and the Windows Media Player host.** Audio goes through
  PipeWire / PulseAudio / ALSA command-line players.
- **MSI parsing and `.lnk` handling.** Not meaningful on Linux.
- **The four alternate main screens.** One resolution-independent layout
  replaces them; MODE SELECT now opens Settings, where the palette lives.
- **Registry shell replacement.** A session entry is the Linux equivalent and
  is reversible by logging out.

## If you want the on-screen keyboard

`OnScreenKeyboard.exe` is not ported because Wayland already has good ones and
a shell-owned keyboard would have to reimplement input-method protocols. Add
to `config.toml`:

```toml
[[programs]]
label = "KEYBOARD"
exec  = "wvkbd-mobintl"
```

## Legal note

LCARS is Paramount's design language and the original LCARS x32 is its
author's work. This is a personal, non-commercial reimplementation for your own
machine, in the same spirit as the many LCARS desktop themes that have existed
since the 1990s. It ships none of the original's code or artwork — only its
plain-text configuration files, its font and its four sound effects, carried
over from the archive you already have. If you plan to publish or distribute
this, that is the part to think about first.
