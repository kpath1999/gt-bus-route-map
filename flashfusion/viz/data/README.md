## Where data is located

Flash-Fusion accuracy is sourced from the following folders based on `llamas.py` and `measure.py`:

* _Bus & WISDM:_ `run_all_remaining_20260528_215519/{bus,wisdm}/benchmark/metrics.csv`
* _ECG:_ `run_ecg_ff_20260529_115359/benchmark/metrics.csv`

This is where I get the average query accuracy for `llama-3.3-70b`.
An experiment would be to conduct the same experiment but using two different models (lower parameter count), to see how the query accuracy scores differ across the three datasets.
If they don't, that is a great win.
I would say -- the LLM judge's model type should be kept constant (`llama-3.3-70b`); it's only the code generation that should be handled by the other model types.