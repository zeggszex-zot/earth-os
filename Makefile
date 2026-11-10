PREFIX ?= /usr
PYTHON ?= python3

.PHONY: help run windowed test shots lint install uninstall models clean \
        splash bootcheck conformance bundle acceptance bench

help:
	@echo "EARTH OS shell"
	@echo
	@echo "  make run        run fullscreen on the current display"
	@echo "  make windowed   run in a window (development)"
	@echo "  make test       run the test suite"
	@echo "  make acceptance the thirteen Earth OS 2.0 acceptance tests"
	@echo "  make bench      measure Earth Mind on this machine"
	@echo "  make shots      render every screen to /tmp/cdos-shots"
	@echo "  make splash     render the boot frame to /tmp/cdos-splash.png"
	@echo "  make bootcheck  measure the boot: one window, frame up first"
	@echo "  make models     download the offline speech models"
	@echo "  make install    install system-wide (needs root; PREFIX=$(PREFIX))"
	@echo "  make uninstall  remove it again"

run:
	$(PYTHON) -m earth_os

windowed:
	$(PYTHON) -m earth_os --windowed

test:
	$(PYTHON) -m unittest discover -s tests -v

# The specification's thirteen tests, one each (docs/TESTING.md).
acceptance:
	$(PYTHON) -m unittest -v tests.test_acceptance

# Earth Mind's own cost on this machine, and the model's if one is running.
bench:
	$(PYTHON) tools/mind_bench.py

# Measure this machine against the capability contract. Needs no GTK and no
# third-party packages, so it runs on a bare Debian or Kali box that has
# nothing installed but python3.
conformance:
	$(PYTHON) tools/conformance.py --json reports/$(shell . /etc/os-release 2>/dev/null && echo $$ID$$VERSION_ID || echo unknown).json

# Compare two machines. A pass is "N mechanisms differed; no capability
# disagreed" — if nothing differed, that is one computer measured twice.
# One file that runs on any machine with python3 and needs nothing beside it.
# Use this for a multi-machine run: it puts identical code on every box and
# prints a digest so you can prove it.
bundle:
	$(PYTHON) tools/conformance.py --bundle cdos-conformance.py

compare:
	@test -n "$(A)" -a -n "$(B)" || { echo "usage: make compare A=reports/debian12.json B=reports/kali.json"; exit 2; }
	$(PYTHON) tools/conformance.py --compare $(A) $(B)

# Preview the boot splash without booting.
splash:
	$(PYTHON) -c "from earth_os import splash; \
	print(splash.render_png('/tmp/cdos-splash.png', 1920, 1080, \
	message='selecting the platform adapter', elapsed=1.2))"

# Boot the real application and measure it. Needs a display; xvfb-run is used
# automatically when there is not one, so this works on a build machine.
# A pass is "boot contract holds": one window throughout, frame up before the
# shell, and animating rather than sitting still.
bootcheck:
	@if [ -n "$$DISPLAY" ] || [ -n "$$WAYLAND_DISPLAY" ]; then \
		$(PYTHON) tools/bootcheck.py; \
	else \
		xvfb-run -a $(PYTHON) tools/bootcheck.py; \
	fi

shots:
	$(PYTHON) tools/screenshot.py --out /tmp/cdos-shots --size 1920x1080

gallery:
	$(PYTHON) tools/gallery.py

doctor:
	$(PYTHON) -m earth_os --doctor

lint:
	@$(PYTHON) -m compileall -q earth_os && echo "compile clean"

models:
	./tools/fetch-models.sh

install:
	./install.sh

uninstall:
	./install.sh --uninstall

clean:
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info
