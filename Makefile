.PHONY: setup check list call all analyze report clean

VENV=.venv
PY=$(VENV)/bin/python
PIP=$(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "Setup done. Copy .env.example to .env and fill it in."

# Validate all API keys before spending money on a call.
check:
	$(PY) -m voicebot.preflight

list:
	$(PY) -m voicebot --list

# Run a single scenario, e.g.: make call SCENARIO=refill
call:
	$(PY) -m voicebot --scenario $(SCENARIO)

# Run every scenario once and write the bug report.
all:
	$(PY) -m voicebot --all --analyze

# Run a batch and analyze: make analyze SCENARIO=refill
analyze:
	$(PY) -m voicebot --scenario $(SCENARIO) --analyze

# Regenerate the bug report from the most recent run's transcripts (no calls placed).
report:
	$(PY) -m analysis.analyze

# Remove all run outputs. The hand-curated top-level BUG_REPORT.md is left alone.
clean:
	rm -rf runs/
