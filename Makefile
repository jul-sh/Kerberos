#      (\_/)
#      (o.o)
#      / >  [nix develop]  <-- provides Python, uv and native libraries
SHELL := ./scripts/run-in-nix.sh
.DEFAULT_GOAL := help

PY := uv run --frozen python

help:
	@echo "Kerberos build targets"
	@echo
	@echo "  make build     Build variable, static and web fonts into fonts/"
	@echo "  make test      Validate the built fonts and their ligatures"
	@echo "  make lint      Google Fonts QA (fontspector, googlefonts profile)"
	@echo "  make proof     Render the README images into build/images/ (published on Pages)"
	@echo "  make site      Assemble the live specimen site into _site/"
	@echo "  make serve     Serve _site/ on http://127.0.0.1:8000"
	@echo "  make release   Package fonts into releases/Kerberos-<version>.zip"
	@echo "  make all       build + test + proof + site + release"
	@echo "  make shell     Open the Nix development shell"
	@echo "  make clean     Remove build outputs"

sync:
	uv sync --frozen

build: sync
	$(PY) scripts/build.py

test:
	$(PY) scripts/validate_weight_mapping.py
	$(PY) scripts/validate_fonts.py
	$(PY) scripts/validate_ligatures.py
	$(PY) scripts/validate_spacing.py

# Excluded: fontdata_namecheck needs network access to an external service;
# repo/dirname_matches_nameid_1 checks the google/fonts repository layout
# (ofl/<family>/), which does not apply to an upstream source repository.
lint:
	mkdir -p out/fontspector
	fontspector --profile googlefonts -l warn --succinct --full-lists --skip-network \
		-x fontdata_namecheck -x googlefonts/repo/dirname_matches_nameid_1 \
		--html out/fontspector/report.html --ghmarkdown out/fontspector/report.md \
		fonts/variable/*.ttf fonts/static/*.ttf

proof:
	$(PY) scripts/proof_readme.py

site: proof
	$(PY) scripts/build_site.py

serve: site
	$(PY) -m http.server 8000 --bind 127.0.0.1 --directory _site

release:
	$(PY) scripts/package_release.py

all: build test lint proof site release

shell:
	bash

clean:
	rm -rf build fonts releases _site out .venv

.PHONY: help sync build test lint proof site serve release all shell clean
