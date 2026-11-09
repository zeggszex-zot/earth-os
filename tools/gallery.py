"""Render a gallery of every LCARS control to a PNG, for visual regression."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cairo
from earth_os.theme import Palette, Strings, ensure_font, font_family
from earth_os.widgets import *
from earth_os.widgets.base import RenderContext

W, H = 1600, 1000
ensure_font()
pal = Palette.builtin("Standard Colors")
st = Strings.builtin("Standard")
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
cr = cairo.Context(surf)
cr.set_source_rgb(0, 0, 0); cr.paint()
ctx = RenderContext(palette=pal, strings=st, width=W, height=H, unit=H/40.0, font=font_family())
u = ctx.u
g = Group()

# Elbows, all four corners
for i, corner in enumerate(("tl", "tr", "bl", "br")):
    g.add(Elbow(f"elb{i}", (40 + i*190, 40, 170, 150), corner=corner,
                bar_w=u(2.2), bar_h=u(1.4), color="frame", label=corner))
# Buttons
g.add(StandardButton("sb", (40, 220, 300, u(1.8)), label="Standard Button", color="accent4"))
g.add(HalfPillButton("hp", (40, 280, 300, u(1.8)), side="left", label="Half Pill", color="accent2"))
g.add(HalfPillButton("hp2", (40, 340, 300, u(1.8)), side="right", label="Half Pill R",
                     color="accent3", label_align="left"))
g.add(FlatButton("fb", (40, 400, 300, u(1.8)), label="Flat", color="accent5"))
g.add(ComplexButton("cb", (40, 460, 300, u(1.8)), label="Complex", color="accent6",
                    cap_color="accent4"))
g.add(TextButton("tb", (40, 520, 720, u(1.6)), label="Initiate Auto Destruct Sequence",
                 color="alert", label_color="accent4"))
g.add(TextButton("tb2", (40, 570, 720, u(1.6)), label="Engineering", color="accent3"))
# Arrows
for i, d in enumerate(("up", "down", "left", "right")):
    g.add(ArrowButton(f"ar{i}", (400 + i*70, 220, 60, 60), direction=d, color="accent2"))
    g.add(ArrowButton(f"as{i}", (400 + i*70, 290, 60, 60), direction=d, style="solid",
                      color="accent4"))
# Bars
g.add(BarRun("bar", (400, 370, 400, u(1.2)), cap_left=True, cap_right=True))
g.add(BarRun("bar2", (400, 420, 400, u(1.2)), weights=(1,2,1,3,1,1,2), animate=False,
             colors=("accent4","accent2","accent5","accent3")))
# Level indicators
for i in range(5):
    g.add(LevelIndicator(f"lv{i}", (840 + i*46, 220, 34, 250), value=0.2+i*0.18,
                         fill_color="accent3", empty_color="accent2"))
g.add(LevelIndicator("lvh", (840, 490, 250, 30), vertical=False, value=0.66))
# Pie
g.add(PieButton("pie", (1100, 220, 200, 200), start=-90, sweep=270, value=0.7,
                color="accent4", label="72%"))
# Meters
for i, nm in enumerate(("CPU", "MEMORY", "DISK")):
    g.add(Meter(f"m{i}", (840, 560 + i*44, 460, 36), label=nm,
                provider=(lambda v: (lambda: (v, f"{int(v*100)}%")))(0.3+i*0.3)))
# Tabs + list
g.add(TabStrip("tabs", (40, 630, 480, u(1.6)), tabs=["Main", "Audio", "Voice", "About"]))
lp = ListPanel("list", (40, 690, 480, 260), items=[f"Program {i:02d}" for i in range(12)])
lp.selected = 3
g.add(lp)
g.add(Label("lab", (560, 640, 700, 40), text="Label / readout text", size=28, color="accent5"))

for e in g:
    e.draw(cr, ctx)
surf.write_to_png("/home/claude/work/gallery.png")
print("ok")
