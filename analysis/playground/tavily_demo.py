from tavily import TavilyClient
import os

api_key = os.getenv("TAVILY_API_KEY")

tavily = TavilyClient(api_key=api_key)

response = tavily.search('Who is Leo Messi?')

print(response)

"""
Yes — **some** of these queries can be answered directly from a CSV like this, some can be answered only as heuristics, and a few should be answered cautiously because the data shown is only two ECG leads plus sparse annotations, not a full clinical interpretation set. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm)

The key distinction is:
- **Directly answerable**: questions that use `time_s`, the waveform columns (`MLII`, `V1`), and `annotation` counts/events. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm)
- **Derivable with signal processing**: heart rate, irregularity, variability, signal quality. [gehealthcare](https://www.gehealthcare.com/insights/article/ecg-signal-quality-a-practical-guide-for-ecg-readings)
- **Weak / limited confidence**: ST-segment changes and broad “concerning patterns,” because those usually need more robust morphology analysis and often more lead coverage. [pmc.ncbi.nlm.nih](https://pmc.ncbi.nlm.nih.gov/articles/PMC10051714/)

## Data supports

From the sample rows, you appear to have:
- `sample_idx`: sample number.
- `time_s`: timestamp in seconds.
- `MLII`, `V1`: ECG lead amplitudes.
- `record_id`: recording identifier.
- `annotation`: event markers or beat/rhythm symbols. [vixra](https://vixra.org/pdf/1301.0056v1.pdf)

That is enough to do beat detection, RR interval analysis, annotation summaries, rough signal quality assessment, and waveform trend analysis. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html)

## Query map

| Query | Can answer? | Confidence | Basic transformations |
|---|---|---|---|
| Are there any irregular heartbeat patterns in this recording? | Yes | Medium | Detect R-peaks from `MLII` or `V1`; compute RR intervals; measure irregularity with RR variance, outlier RR gaps, and annotation review. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html) |
| What is the average heart rate across this recording? | Yes | High | Detect R-peaks; count beats; compute average HR from beat times or mean RR interval. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html) |
| Were there any periods of abnormal cardiac activity? | Yes | Medium | Segment the signal into windows; detect abnormal beat density, rhythm changes, or annotation clusters by time. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm) |
| How does the cardiac signal vary over time? | Yes | Medium | Window the recording; compute HR, RR variability, amplitude range, baseline drift, and maybe beat morphology summaries over time. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html) |
| Were there any significant annotation events in this recording? | Yes | High | Parse non-empty `annotation`; map symbols to known meanings; count and timestamp each event type. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm) |
| What is the overall quality of the ECG signal? | Yes | Medium | Estimate noise, baseline wander, clipping, missingness, lead disagreement, and R-peak detectability over windows. [gehealthcare](https://www.gehealthcare.com/insights/article/ecg-signal-quality-a-practical-guide-for-ecg-readings) |
| Are there any concerning patterns in the cardiac data? | Partly | Low-Medium | Combine arrhythmia heuristics, extreme HR, abnormal annotations, and poor morphology consistency; return as screening only, not diagnosis. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm) |
| When during the recording was cardiac activity most irregular? | Yes | Medium | Compute irregularity score per time window from RR dispersion and abnormal annotation density; return top windows. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html) |
| How many beats were annotated as abnormal? | Yes | High | Count annotation symbols belonging to abnormal beat classes. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm) |
| Give me a summary of this ECG recording. | Yes | Medium | Aggregate duration, estimated HR, rhythm regularity, annotation counts, signal quality, and notable windows. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html) |
| What beat types appear most frequently in this recording? | Yes | High | Group annotations by beat type; count and rank frequencies. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm) |
| Is there any evidence of ST-segment changes in the signal? | Partly | Low | Detect beats; estimate baseline and J-point/ST segment level per beat; compare over time, but report as tentative because single/few-lead ST analysis is limited. [pmc.ncbi.nlm.nih](https://pmc.ncbi.nlm.nih.gov/articles/PMC10051714/) |

## Basic transformations

Below is the simplest practical transformation plan for each query.

### 1. Irregular heartbeat patterns
- Clean the signal lightly, typically remove baseline wander and obvious high-frequency noise. [pmc.ncbi.nlm.nih](https://pmc.ncbi.nlm.nih.gov/articles/PMC8708403/)
- Detect R-peaks in `MLII`; use `V1` as fallback or cross-check. [python-heart-rate-analysis-toolkit.readthedocs](https://python-heart-rate-analysis-toolkit.readthedocs.io/en/latest/algorithmfunctioning.html)
- Compute RR intervals: \(RR_i = t_{i+1} - t_i\).
- Flag irregularity using:
  - High RR standard deviation.
  - Large adjacent RR changes.
  - Windows with many premature/abnormal annotations. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html)

Concrete answer form:
- “Yes, irregularity appears between 120s and 150s, where RR intervals become more dispersed than the baseline.”

### 2. Average heart rate
- Detect R-peaks. [python-heart-rate-analysis-toolkit.readthedocs](https://python-heart-rate-analysis-toolkit.readthedocs.io/en/latest/algorithmfunctioning.html)
- Get beat timestamps.
- Compute either:
  - \(HR_{avg} = \frac{(N-1)\cdot 60}{t_{last}-t_{first}}\), or
  - \(HR_{avg} = 60 / \text{mean}(RR)\). [indigits](https://www.indigits.com/post/2022/10/ecg_python/)

Concrete answer form:
- “Average heart rate is 78 bpm.”

### 3. Periods of abnormal cardiac activity
- Divide the recording into fixed windows, such as 10 or 30 seconds.
- For each window compute:
  - Abnormal annotation count.
  - HR range.
  - RR irregularity score.
  - Beat morphology deviation from median beat template. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm)
- Mark windows exceeding thresholds.

Concrete answer form:
- “Abnormal activity was concentrated from 8:10 to 8:40, with frequent abnormal annotations and elevated RR variability.”

### 4. Signal variation over time
- Window the recording by time.
- Within each window calculate:
  - Mean HR.
  - RR variability.
  - Amplitude range of `MLII`/`V1`.
  - Baseline drift estimate. [gehealthcare](https://www.gehealthcare.com/insights/article/ecg-signal-quality-a-practical-guide-for-ecg-readings)
- Optionally compare beginning/middle/end.

Concrete answer form:
- “The signal is stable early, becomes noisier midway, and shows increased beat-to-beat variability near the end.”

### 5. Significant annotation events
- Filter rows where `annotation` is non-empty.
- Parse symbol values such as beat labels, rhythm changes, or waveform markers like `+` where applicable. [vixra](https://vixra.org/pdf/1301.0056v1.pdf)
- Map symbols to human-readable meanings using MIT-BIH conventions when appropriate. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm)

Concrete answer form:
- “There were 12 ventricular ectopic beats and 1 rhythm change marker.”

### 6. ECG signal quality
- Compute per-window signal quality features:
  - Missing values.
  - Flatline duration.
  - Saturation/clipping.
  - Baseline wander.
  - High-frequency noise.
  - R-peak detection success rate. [frontiersin](https://www.frontiersin.org/journals/neuroscience/articles/10.3389/fnins.2023.1153386/full)
- Optionally compare lead consistency between `MLII` and `V1`.

Concrete answer form:
- “Overall signal quality is fair: low missingness, moderate baseline drift, and reduced quality in the final quarter.”

### 7. Concerning patterns
- This is not directly a diagnosis, so treat it as a risk-screening summary. [pmc.ncbi.nlm.nih](https://pmc.ncbi.nlm.nih.gov/articles/PMC10051714/)
- Combine:
  - Extreme average or local HR.
  - Long pauses.
  - Frequent abnormal beats.
  - Strong irregularity bursts.
  - Possible ST drift, if detectable.
- Return cautious language.

Concrete answer form:
- “The main concerning pattern is a cluster of abnormal beats with irregular timing around 5 minutes; this warrants review.”

### 8. Most irregular time
- Split into windows.
- Build an irregularity score, for example:
  - RR std + proportion of RR outliers + abnormal annotation density. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html)
- Return the highest-scoring window.

Concrete answer form:
- “Cardiac activity was most irregular from 302s to 332s.”

### 9. Abnormal beat count
- Define which annotations count as abnormal, for example ventricular, supraventricular, fusion, paced, unclassifiable, depending on your label set. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm)
- Count rows with those symbols.

Concrete answer form:
- “There were 47 beats annotated as abnormal.”

### 10. ECG summary
- Aggregate:
  - Duration from `time_s`.
  - Estimated HR.
  - Rhythm regularity.
  - Main annotation types.
  - Signal quality.
  - Most notable abnormal windows. [gehealthcare](https://www.gehealthcare.com/insights/article/ecg-signal-quality-a-practical-guide-for-ecg-readings)

Concrete answer form:
- “This 30-minute ECG shows an average HR of 76 bpm, mostly regular rhythm, occasional abnormal beats, and moderate baseline noise.”

### 11. Most frequent beat types
- Parse annotation symbols.
- Group by symbol or mapped beat class.
- Rank counts descending. [physionet](https://physionet.org/physiobank/database/html/mitdbdir/intro.htm)

Concrete answer form:
- “Normal beats were most frequent, followed by ventricular ectopic beats.”

### 12. ST-segment changes
- This one is the trickiest.
- Required basic workflow:
  - Filter and stabilize baseline. [gehealthcare](https://www.gehealthcare.com/insights/article/ecg-signal-quality-a-practical-guide-for-ecg-readings)
  - Detect R-peaks and define beat boundaries.
  - Estimate J-point and ST segment level relative to an isoelectric baseline for each beat.
  - Track ST deviation over time.
- But with only `MLII` and `V1`, confidence is limited; single-lead or limited-lead ST interpretation is weaker than multi-lead clinical ECG interpretation. [sciencedirect](https://www.sciencedirect.com/science/article/abs/pii/S0167527321018544)

Concrete answer form:
- “There may be mild ST-level drift in parts of the recording, but this is only a tentative signal-based observation and not enough for a clinical conclusion.”

## What is likely inaccurate today

If your current system is doing anything like “mean gradient of MLII = frequency = heart rate,” that will be wrong for heart-rate and irregularity questions. For ECG data, the essential primitive transformation is almost always: [python-heart-rate-analysis-toolkit.readthedocs](https://python-heart-rate-analysis-toolkit.readthedocs.io/en/latest/algorithmfunctioning.html)

- preprocess signal,
- detect R-peaks,
- derive beat times,
- compute RR-based features,
- then answer the question. [arpnjournals](http://www.arpnjournals.org/jeas/research_papers/rp_2016/jeas_1216_5418.pdf)

## Best minimal pipeline

If you want one shared ECG feature pipeline that supports most of these queries, use this order:

1. Validate columns: `time_s`, one lead (`MLII` preferred), optional second lead, `annotation`.
2. Preprocess lead: detrend / remove baseline wander, optional bandpass. [pmc.ncbi.nlm.nih](https://pmc.ncbi.nlm.nih.gov/articles/PMC8708403/)
3. Detect R-peaks. [neuropsychology.github](https://neuropsychology.github.io/NeuroKit/functions/ecg.html)
4. Derive:
   - beat timestamps,
   - RR intervals,
   - HR time series,
   - abnormal annotation table,
   - per-window quality metrics.
5. Answer queries off those derived tables.

A very practical internal feature set would be:
- `beats_df`: `beat_time`, `rr_s`, `inst_hr_bpm`, `lead`, `annotation`
- `windows_df`: `window_start`, `window_end`, `mean_hr`, `rr_std`, `abnormal_count`, `noise_score`, `baseline_wander_score`
- `ann_df`: `time_s`, `annotation`, `annotation_type`

That one transformation layer would support about 10 of your 12 queries well, and the last two — “concerning patterns” and “ST-segment changes” — should be framed as cautious screening outputs rather than definitive findings. [pmc.ncbi.nlm.nih](https://pmc.ncbi.nlm.nih.gov/articles/PMC10051714/)

Would you like me to turn this into a query-to-transformation routing table in Python so your agent can map each ECG query type to a concrete processing recipe?
"""