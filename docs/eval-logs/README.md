# Evaluation logs

The terminal output behind `../eval-baseline.md`, kept so that every number on that page can be counted
again without the key. Nothing here is read by the service or by a test. The files are grouped by what they
prove.

## `baseline/` — the first comparison, 144 runs

Three passes run back to back on the morning of 2026-10-10, 48 runs each.

| File | Produced (UTC) | Command | Prompt text sent | Model returned |
|---|---|---|---|---|
| `baseline/pass-1-triage_v1.log` | 06:29 to 06:32 | `python -m evals.run --runs 3` | `triage_v1`, as committed | `gpt-4.1-mini-2025-04-14` |
| `baseline/pass-2-triage_v2-earlier-draft.log` | 06:34 to 06:37 | `python -m evals.run --runs 3` | an earlier draft of `triage_v2` that was never committed, so this pass cannot be run again | `gpt-4.1-mini-2025-04-14` |
| `baseline/pass-3-triage_v2-as-committed.log` | 06:39 to 06:42 | `python -m evals.run --runs 3` | `triage_v2`, as committed | `gpt-4.1-mini-2025-04-14` |

Which prompt each of these three sent is what the person who ran them recorded; the files do not say it. No
event carries the prompt version or the case id, and the command did not yet print a header line.

The files do bear it out, through the input tokens of the first model call of each run, which depend on the
prompt, the tool descriptions and the schema ("The first model call of each case" below). Pass 3 sends the
same count as the evening pass in `shipped/`, whose own header line says `triage_v2@fc8d2044`, in every one
of the twelve prefix cases (2732, 2807, 2893, 2992, ...): pass 3 sent the committed `triage_v2`. Pass 2
sends exactly 83 fewer in every case, a shorter draft. Pass 1 sends exactly 359 fewer, against 351 for the
two prompt files counted with the vendor's tokenizer plus about 13 for the tool description reworded in the
same commit — within 5 tokens, not exact.

## `shipped/` — the shipped configuration, measured on the final code

| File | Produced (UTC) | Command | Prompt version and model |
|---|---|---|---|
| `shipped/triage_v2-gpt-4.1-mini-3-runs.log` | 2026-10-10 14:41 to 14:44 | `python -m evals.run --prompt triage_v2 --runs 3` | in the file's header line: `triage_v2@fc8d2044`, requested `gpt-4.1-mini`, returned `gpt-4.1-mini-2025-04-14` |
| `shipped/served-walkthrough.txt` | 2026-10-10 13:42 to 13:46 | every request of the root `README.md` sent with `curl` to the service started with `docker compose up` and the image's own command | `triage_v2`, the file the served service loads; no line of the file says it |

The evening pass ran on the code as it is at the commit that added this folder; that is what the person who
ran it recorded, and the hash in the header is the one `tests/unit/test_prompt_files.py` pins for
`triage_v2`. The walkthrough is one line per request: status, selected headers, selected fields. It is
older than the pass by an hour: the commits between them changed, under `backend/` and in `main.py`, only
the new file `triage_v3.md`, which the served service does not load, and an optional `prompt` argument of
`create_app` that the image's command does not pass. Unlike the logs it holds text the model wrote (one
`reply`, one refund `reason`) and one search query.

## `experiments/` — measured, and not shipped

| File | Produced (UTC) | Command | Prompt version and model |
|---|---|---|---|
| `experiments/triage_v3-gpt-4.1-mini-3-runs.log` | 2026-10-10 14:36 to 14:40 | `python -m evals.run --prompt triage_v3 --runs 3` | in the header line: `triage_v3@3df4a585`, requested `gpt-4.1-mini`, returned `gpt-4.1-mini-2025-04-14` |
| `experiments/triage_v2-gpt-4.1-refused.log` | 2026-10-10 14:44 | the same command as the pass in `shipped/` with `TRIAGE_MODEL=gpt-4.1`, chained after it in one terminal | none measured: the vendor refused the model on the first call, and the command ended with 2 |
| `experiments/gpt-5-mini-smoke-refused.txt` | 2026-10-10, evening | `python -m evals.smoke` with `TRIAGE_MODEL=gpt-5-mini` and `TRIAGE_REASONING_EFFORT=low` | none measured: refused the same way; two lines, the note of the person who ran it and the line the command printed |
| `experiments/gpt-5-mini-smoke-out-of-credit.txt` | 2026-10-10, later that evening | the same command | none measured: the vendor answered `429 credit_balance_exhausted` and the command reported misconfigured, which the adapter does for a 429 whose error type is `insufficient_quota`; the type of this answer was not recorded; two lines, as above |

The pass in `shipped/` and the refused attempt were one terminal output, two commands chained; it was cut
between the last line the first command printed and the first container line of the second, and nothing was
removed: the two files put end to end are the output. That is why the file in `shipped/` has no
`[exited with code ...]` line, which the terminal added once, after the second command.

## What a log file holds

The whole output of one `python -m evals.run`, unedited: the container lines Docker prints first, one JSON
line per logged event, then what the command prints — in the evening files a header line, the table and
five totals lines; in the morning files the table and three totals lines — and the line the terminal added
when the command ended. The events are logged at INFO, where a line has no message text and no `payload`:
nothing a customer wrote and nothing the model wrote is in the `.log` files. The keys are `ts`, the four
ids, `event`, `outcome`, `duration_ms`, `kind`, `model`, `input_tokens`, `output_tokens`, `tool`, `urgency`,
`version`, `source`, `overrides`, `transition` and `actor`. Event names and keys: `../logging-and-errors.md`.

## The two table formats

The morning tables have nine columns. The evening tables have a header line above them, two more columns —
"effects proposed" and "first call in", between "runs differ" and "latency s" — and two more totals lines,
`incident proposed` and `refund proposed`. In both formats "tools missed" is the fifth column, "runs
differ" the seventh and the tokens the last, so the commands below that read those work on every file.
**Three commands work on the evening format only** and say so: the header line, the two totals lines of
proposed effects, and the "effects proposed" and "first call in" columns. The morning files have the same
facts as events, which the commands under "Counted from the events" read in both.

"effects proposed" and its totals lines count the effect-tool calls of the scored turn only; for
`OP-QUESTION` and `OP-CORRECTION` that is the operator's turn, so a call made while their ticket was first
triaged is in the events and not in the column.

## Counting the numbers from the files

Each command is one line, run from the repository root in `bash`; under it is what it printed on
2026-10-10. `docs/eval-logs/*/*.log` is all six log files, `docs/eval-logs/*/*-3-runs.log` the two evening
passes.

### What was run

The header line of each evening pass (evening format only).

```sh
grep -H '^prompt ' docs/eval-logs/*/*.log
```

```
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:prompt triage_v3@3df4a585 | model requested gpt-4.1-mini, returned gpt-4.1-mini-2025-04-14 | runs 3 | started 2026-10-10T14:36:36Z
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:prompt triage_v2@fc8d2044 | model requested gpt-4.1-mini, returned gpt-4.1-mini-2025-04-14 | runs 3 | started 2026-10-10T14:41:08Z
```

The first event of each file, which dates it (UTC).

```sh
for f in docs/eval-logs/*/*.log; do echo "$f $(grep -m1 -o '"ts": "[^"]*"' "$f")"; done
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log "ts": "2026-10-10T06:29:01.504806+00:00"
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log "ts": "2026-10-10T06:34:28.767258+00:00"
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log "ts": "2026-10-10T06:39:08.052662+00:00"
docs/eval-logs/experiments/triage_v2-gpt-4.1-refused.log "ts": "2026-10-10T14:44:48.738160+00:00"
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log "ts": "2026-10-10T14:36:37.061238+00:00"
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log "ts": "2026-10-10T14:41:08.413903+00:00"
```

The model id the vendor returned, on every model call; `null` is the one call the vendor refused.

```sh
grep -o '"model": [^,}]*' docs/eval-logs/*/*.log | sort | uniq -c
```

```
 112 docs/eval-logs/baseline/pass-1-triage_v1.log:"model": "gpt-4.1-mini-2025-04-14"
 108 docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:"model": "gpt-4.1-mini-2025-04-14"
 113 docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:"model": "gpt-4.1-mini-2025-04-14"
   1 docs/eval-logs/experiments/triage_v2-gpt-4.1-refused.log:"model": null
 111 docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:"model": "gpt-4.1-mini-2025-04-14"
 113 docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:"model": "gpt-4.1-mini-2025-04-14"
```

Runs per file: a run starts with the turn that takes the ticket in. 48 is 16 cases × 3 runs; the three
morning passes are the 144 runs; the refused attempt stopped in its first.

```sh
grep -c '"kind": "ingest"' docs/eval-logs/*/*.log
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log:48
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:48
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:48
docs/eval-logs/experiments/triage_v2-gpt-4.1-refused.log:1
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:48
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:48
```

### Printed under the tables

Urgency, next action and failed runs: the three lines under each printed table.

```sh
grep -E '^(urgency|next_action|failed runs)' docs/eval-logs/*/*.log
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log:urgency: 39 pass, 9 miss, 0 violation
docs/eval-logs/baseline/pass-1-triage_v1.log:next_action: 34 pass, 14 miss, 0 violation
docs/eval-logs/baseline/pass-1-triage_v1.log:failed runs (a forbidden value or a forbidden effect): 0
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:urgency: 42 pass, 6 miss, 0 violation
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:next_action: 41 pass, 7 miss, 0 violation
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:failed runs (a forbidden value or a forbidden effect): 0
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:urgency: 39 pass, 9 miss, 0 violation
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:next_action: 41 pass, 7 miss, 0 violation
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:failed runs (a forbidden value or a forbidden effect): 0
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:urgency: 40 pass, 8 miss, 0 violation
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:next_action: 44 pass, 4 miss, 0 violation
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:failed runs (a forbidden value or a forbidden effect): 0
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:urgency: 41 pass, 7 miss, 0 violation
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:next_action: 42 pass, 6 miss, 0 violation
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:failed runs (a forbidden value or a forbidden effect): 0
```

Effects proposed in the scored turn (evening format only).

```sh
grep -E '^(incident|refund) proposed' docs/eval-logs/*/*.log
```

```
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:incident proposed (calls over all runs): 1
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:refund proposed (calls over all runs): 0
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:incident proposed (calls over all runs): 4
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:refund proposed (calls over all runs): 0
```

Required tools not called, summed over the "tools missed" column of each printed table.

```sh
for f in docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log; do echo "$f"; awk -F '  +' '/^(T[0-9]|OP-|ADV-)/ {n = split($5, word, /[ ,]+/); for (i = 2; i <= n; i++) if (word[i] ~ /^x[0-9]+$/) missed[word[i-1]] += substr(word[i], 2)} END {for (tool in missed) print "  " tool, missed[tool]}' "$f" | sort; done
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log
  kb_search 5
  open_incident 9
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log
  open_incident 8
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log
  open_incident 4
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log
  open_incident 8
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log
  open_incident 5
```

The same column case by case, in the passes that sent `triage_v2` as committed and `triage_v3`: of each
case's three runs, how many did not call `open_incident`.

```sh
for f in docs/eval-logs/baseline/pass-3-*.log docs/eval-logs/*/*-3-runs.log; do echo "$f"; awk -F '  +' '/^T2-[234]/ {print "  " $1, $5}' "$f"; done
```

```
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log
  T2-2 open_incident x3
  T2-3 -
  T2-4 open_incident x1
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log
  T2-2 open_incident x3
  T2-3 open_incident x2
  T2-4 open_incident x3
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log
  T2-2 open_incident x3
  T2-3 open_incident x1
  T2-4 open_incident x1
```

Cases whose identical runs disagree: the rows with `yes` in the "runs differ" column.

```sh
for f in docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log; do echo "$f $(awk -F '  +' '/^(T[0-9]|OP-|ADV-)/ && $7 == "yes"' "$f" | wc -l | tr -d ' ')"; done
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log 10
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log 11
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log 10
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log 9
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log 8
```

The "effects proposed" and "first call in" columns of the two evening passes side by side, `triage_v2` then
`triage_v3` (evening format only); the last line counts the cases whose first call is exactly 167 input
tokens longer with `triage_v3`, the text it adds: all fourteen that print one number. The two operator
cases print a range, because their scored turn follows a first turn whose length varies.

```sh
join -t '|' <(awk -F '  +' '/^(T[0-9]|OP-|ADV-)/ {print $1 "|" $8 "|" $9}' docs/eval-logs/shipped/*.log | sort) <(awk -F '  +' '/^(T[0-9]|OP-|ADV-)/ {print $1 "|" $8 "|" $9}' docs/eval-logs/experiments/triage_v3-*.log | sort) | awk -F '|' '{print; if ($5 - $3 == 167) same++} END {print same " cases differ by exactly 167"}'
```

```
ADV-INJECTION|-|2955|-|3122
ADV-TOOL-DOWN|-|2731|-|2898
OP-CORRECTION|-|3418-3514|-|3583-4068
OP-QUESTION|-|3285-3328|-|3478-3518
T1-1|-|2732|-|2899
T1-2|-|2807|-|2974
T1-3|-|2893|-|3060
T1-4|-|2992|-|3159
T2-1|-|2731|-|2898
T2-2|-|2809|-|2976
T2-3|incident x2|2902|incident x1|3069
T2-4|incident x2|2986|-|3153
T3-1|-|2729|-|2896
T3-2|-|2810|-|2977
T3-3|-|2892|-|3059
T3-4|-|2973|-|3140
14 cases differ by exactly 167
```

### Counted from the events

Calls per tool, each `tool.call` event counted once, in every turn of the pass.

```sh
for f in docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log; do echo "$f"; grep '"event": "tool.call"' "$f" | grep -o '"tool": "[a-z_]*"' | sort | uniq -c; done
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log
  24 "tool": "billing_lookup"
   7 "tool": "kb_search"
   9 "tool": "propose_refund"
  18 "tool": "status_check"
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log
  23 "tool": "billing_lookup"
  12 "tool": "kb_search"
   1 "tool": "open_incident"
  18 "tool": "status_check"
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log
  24 "tool": "billing_lookup"
  12 "tool": "kb_search"
   5 "tool": "open_incident"
  18 "tool": "status_check"
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log
  24 "tool": "billing_lookup"
  12 "tool": "kb_search"
   1 "tool": "open_incident"
   2 "tool": "propose_refund"
  18 "tool": "status_check"
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log
  24 "tool": "billing_lookup"
  13 "tool": "kb_search"
   4 "tool": "open_incident"
  18 "tool": "status_check"
```

Which run made each effect call, and in which turn of the run. Runs are numbered in the order of the file;
the cases run in the order of the table, each case's runs one after another, so in a pass of three runs the
case is row `(run - 1) / 3 + 1`, rounded down: row 4 is T1-4, 6 is T2-2, 7 is T2-3, 8 is T2-4, 14 is
OP-CORRECTION. `ingest` is the turn that takes the ticket in; for row 14 the scored turn is the `operator`
turn after it, so a call in its `ingest` turn is not in "effects proposed".

```sh
for f in docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log; do echo "$f"; awk '/"event": "turn.started"/ {match($0, /"kind": "[a-z_]*"/); kind = substr($0, RSTART + 9, RLENGTH - 10); if (kind == "ingest") run++} /"event": "tool.call"/ && /"tool": "(open_incident|propose_refund)"/ {match($0, /"tool": "[a-z_]*"/); calls[run " " kind " " substr($0, RSTART + 9, RLENGTH - 10)]++} END {for (key in calls) {split(key, part, " "); print "  run " part[1] ", row " int((part[1] - 1) / 3) + 1 ", " part[2] " turn: " part[3] " x" calls[key]}}' "$f" | sort -k2 -n; done
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log
  run 12, row 4, ingest turn: propose_refund x2
  run 40, row 14, ingest turn: propose_refund x2
  run 41, row 14, ingest turn: propose_refund x2
  run 42, row 14, ingest turn: propose_refund x3
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log
  run 19, row 7, ingest turn: open_incident x1
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log
  run 19, row 7, ingest turn: open_incident x1
  run 20, row 7, ingest turn: open_incident x1
  run 21, row 7, ingest turn: open_incident x1
  run 22, row 8, ingest turn: open_incident x1
  run 24, row 8, ingest turn: open_incident x1
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log
  run 21, row 7, ingest turn: open_incident x1
  run 42, row 14, ingest turn: propose_refund x2
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log
  run 19, row 7, ingest turn: open_incident x1
  run 21, row 7, ingest turn: open_incident x1
  run 22, row 8, ingest turn: open_incident x1
  run 24, row 8, ingest turn: open_incident x1
```

Actions the proposals created: `action.transition` events whose transition is `proposed`. In the first pass
9 calls created 8 actions.

```sh
grep -c '"transition": "proposed"' docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log:8
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:1
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:5
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:3
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:4
```

The first model call of each case: the input tokens of the first model call of the first run of each of the
twelve prefix cases, T1-1 to T3-4. Equal lines sent the same prompt, tool descriptions and schema.

```sh
for f in docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log; do echo "$f"; awk '/"kind": "ingest"/ {run++; is_first = 1} /"event": "llm.call"/ && is_first {is_first = 0; if (run <= 36 && run % 3 == 1) {match($0, /"input_tokens": [0-9]+/); printf " %s", substr($0, RSTART + 16, RLENGTH - 16)}} END {print ""}' "$f"; done
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log
 2373 2448 2534 2633 2372 2450 2543 2627 2370 2451 2533 2614
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log
 2649 2724 2810 2909 2648 2726 2819 2903 2646 2727 2809 2890
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log
 2732 2807 2893 2992 2731 2809 2902 2986 2729 2810 2892 2973
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log
 2899 2974 3060 3159 2898 2976 3069 3153 2896 2977 3059 3140
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log
 2732 2807 2893 2992 2731 2809 2902 2986 2729 2810 2892 2973
```

The refused attempt, whole, without its ids: one turn started, one model call answered `misconfigured` with
no model id, the line the command printed, and the exit code.

```sh
grep -v Container docs/eval-logs/experiments/triage_v2-gpt-4.1-refused.log | sed -E 's/"(request_id|conversation_id|turn_id)": "[^"]*", //g'
```

```
{"ts": "2026-10-10T14:44:48.738160+00:00", "event": "turn.started", "action_id": null, "duration_ms": null, "outcome": "running", "kind": "ingest"}
{"ts": "2026-10-10T14:44:49.421076+00:00", "event": "llm.call", "action_id": null, "duration_ms": 680, "outcome": "misconfigured", "model": null}
the model cannot be used: the model rejected the service's configuration: PermissionDeniedError 403 model_not_found; the request is stored and nothing was decided


[exited with code 2]
```

The refused smoke call.

```sh
tail -1 docs/eval-logs/experiments/gpt-5-mini-smoke-refused.txt
```

```
{"ok": false, "error": "LLMMisconfigured", "detail": "PermissionDeniedError 403 model_not_found"}
```

### The first comparison in detail

The full first thread (T1-4) in the five passes: its accepted action is `escalate_to_human`, so the first
number of the action column is the runs that escalated; 2 + 2 + 3 = 7 of 9 in the morning passes.

```sh
grep '^T1-4' docs/eval-logs/*/pass-*.log docs/eval-logs/*/*-3-runs.log | awk '{print $1, "action p/m/v", $3}'
```

```
docs/eval-logs/baseline/pass-1-triage_v1.log:T1-4 action p/m/v 2/1/0
docs/eval-logs/baseline/pass-2-triage_v2-earlier-draft.log:T1-4 action p/m/v 2/1/0
docs/eval-logs/baseline/pass-3-triage_v2-as-committed.log:T1-4 action p/m/v 3/0/0
docs/eval-logs/experiments/triage_v3-gpt-4.1-mini-3-runs.log:T1-4 action p/m/v 3/0/0
docs/eval-logs/shipped/triage_v2-gpt-4.1-mini-3-runs.log:T1-4 action p/m/v 3/0/0
```

The last decision of each T1-4 run (runs 10 to 12) of the three morning passes, counted by action: 7
escalated, 2 routed, none answered unattended.

```sh
for f in docs/eval-logs/baseline/pass-*.log; do awk '/"kind": "ingest"/ {run++} /"event": "decision.made"/ && run >= 10 && run <= 12 {match($0, /"outcome": "[a-z_]*"/); last[run] = substr($0, RSTART + 12, RLENGTH - 13)} END {for (run in last) print last[run]}' "$f"; done | sort | uniq -c
```

```
   7 escalate_to_human
   2 route_to_specialist
```

Input tokens of the third morning pass minus the second, case by case, from the last column of the two
tables; then the number of cases where the difference is exactly 498.

```sh
join <(awk '/^(T[0-9]|OP-|ADV-)/ {split($NF, tokens, "/"); print $1, tokens[1]}' docs/eval-logs/baseline/pass-2-*.log | sort) <(awk '/^(T[0-9]|OP-|ADV-)/ {split($NF, tokens, "/"); print $1, tokens[1]}' docs/eval-logs/baseline/pass-3-*.log | sort) | awk '{print $1, $3 - $2; if ($3 - $2 == 498) same++} END {print same " cases differ by exactly 498"}'
```

```
ADV-INJECTION 747
ADV-TOOL-DOWN 498
OP-CORRECTION 902
OP-QUESTION 4290
T1-1 498
T1-2 498
T1-3 498
T1-4 498
T2-1 498
T2-2 498
T2-3 7383
T2-4 7437
T3-1 498
T3-2 402
T3-3 496
T3-4 370
8 cases differ by exactly 498
```

The tokens column is the sum over the case's runs of the tokens logged on each model call; here for the
first case of the first pass (runs 1 to 3), which the table prints as 14685/505.

```sh
awk '/"kind": "ingest"/ {run++} /"event": "llm.call"/ && run <= 3 {match($0, /"input_tokens": [0-9]+/); tokens_in += substr($0, RSTART + 16, RLENGTH - 16); match($0, /"output_tokens": [0-9]+/); tokens_out += substr($0, RSTART + 17, RLENGTH - 17)} END {print tokens_in "/" tokens_out}' docs/eval-logs/baseline/pass-1-triage_v1.log
```

```
14685/505
```

### What the files do not hold

No key-shaped string, e-mail address, path of the machine that ran them or `payload` in any file of the
three folders. It printed nothing.

```sh
grep -n -i -E 'sk-|proj_|bearer|payload|/Users/|/home/|/private/|@[a-z0-9.-]+\.[a-z]{2,}' docs/eval-logs/*/*.log docs/eval-logs/*/*.txt
```

Every key of every JSON line of the six log files, with the number of lines that carry it.

```sh
grep -h '^{' docs/eval-logs/*/*.log | grep -o '"[a-z_]*":' | sort | uniq -c
```

```
1440 "action_id":
  54 "actor":
1440 "conversation_id":
1440 "duration_ms":
1440 "event":
 557 "input_tokens":
 271 "kind":
 558 "model":
1440 "outcome":
 557 "output_tokens":
 270 "overrides":
1440 "request_id":
 270 "source":
 287 "tool":
  54 "transition":
1440 "ts":
1440 "turn_id":
 270 "urgency":
 270 "version":
```
