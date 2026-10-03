# DayOne — paper maternal registry -> verified digital record (100 % local)
PY := uv run python
# evaluation run (see docs/EVALUATION.md §7)
RUN ?= final
.PHONY: install models fonts prepare dataset eval calibrate report test lint demo demo-offline clean-demo

install:            ## Python environment (uv)
	uv sync

models:             ## local vision models (Ollama must be running)
	ollama pull qwen3.5:9b
	ollama pull glm-ocr

fonts:              ## open handwriting fonts used to synthesise Arabic / English pages
	mkdir -p artifacts/fonts
	for f in ofl/arefruqaa/ArefRuqaa-Regular.ttf ofl/caveat/Caveat%5Bwght%5D.ttf ofl/reemkufiink/ReemKufiInk-Regular.ttf \
	         ofl/gaegu/Gaegu-Regular.ttf ofl/patrickhand/PatrickHand-Regular.ttf ofl/marhey/Marhey%5Bwght%5D.ttf; do \
	  n=$$(basename $$f | sed 's/%5Bwght%5D/-VF/'); curl -sfL -o artifacts/fonts/$$n https://github.com/google/fonts/raw/main/$$f; done

prepare:            ## ground truth from the specimen PDF + blank templates
	$(PY) -m dayone.evaluation.groundtruth
	$(PY) -m dayone.forms.templates

dataset: prepare fonts  ## simulated captures (seeded) + multilingual pages
	$(PY) -m dayone.evaluation.dataset

eval:               ## run the pipeline on every capture (resumable; ~3 h on an M4 Pro)
	$(PY) -m dayone.evaluation.run --run $(RUN)

calibrate:          ## fit the confidence model and the acceptance threshold (calibration split)
	$(PY) -m dayone.evaluation.report calibrate --run $(RUN)

report:             ## metrics on the test split -> docs/RESULTS.md
	$(PY) -m dayone.evaluation.report report --run $(RUN)

test:               ## unit, offline-robustness and end-to-end tests (no AI needed)
	uv run pytest -q

lint:
	uv run ruff check src tests

demo:               ## phone http://127.0.0.1:8000 + server http://127.0.0.1:8100/dashboard
	$(PY) -m dayone.demo

demo-offline:       ## same, the phone starts without network
	$(PY) -m dayone.demo --offline

clean-demo:
	rm -rf artifacts/demo
