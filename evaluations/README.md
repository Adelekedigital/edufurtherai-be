# Model evaluation

Place one JSON object per line in `samples.jsonl` with this shape:

```json
{"id":"sample-001","task":"scholarship_extraction","source_data":{"excerpt":"..."},"expected":{"candidate":{},"evidence":[]}}
```

Run the harness against configured task models with:

```powershell
$env:PYTHONPATH = "src"
uv run python evaluations/run.py evaluations/samples.jsonl
```

Validate the dataset without provider calls:

```powershell
uv run python evaluations/run.py evaluations/samples.jsonl --validate-only
```

The report measures schema validity and exact critical-field matches for the supplied labels.
It is an evaluation aid, not evidence that the 95% launch gate has been met.
