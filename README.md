# cascade-cms

A typed, async REST client for Hannon Hill Cascade CMS.

[Full Documentation](https://sharkdroid.github.io/wiki/cascade-cms-wiki/)

## Usage

```python
from cascade_cms.cmstypes import Asset, IdentifierType
from cascade_cms.wrapper import CascadeWrapperBase

environment_variables = {
    "API_KEY": "...",
    "CASCADE_URL": "...",
    "SERVER": "prod",  # label used for logfile naming
}

with CascadeWrapperBase(environment_variables) as cascade:
    identifier = IdentifierType(identifier="e868f539ac1001062cfa029c4c5df4d0", asset_type="folder")
    cascade.operations.read(identifier)
    results = cascade.submit_requests(Asset)
```

Operations that take an identifier (`read`, `delete`, `copy`, `move`, `publish`, `checkIn`, `checkOut`,
`listSubscribers`, `readAccessRights`, `readWorkflowSettings`, `readWorkflowInformation`,
`performWorkflowTransition`) accept either an `IdentifierType` (asset type + UUID) or a `Path`
(asset type + site name + site-relative path) — see `cascade_cms.cmstypes.resolve_identifier`.

See `examples/read_and_update_asset.py` for a fuller walkthrough.

### Operation chains

Every `cascade.operations.<op>()` call starts an **operation chain** and returns it. Chaining
`.then(callback)` or another operation onto it adds a step to *that* chain; a fresh
`cascade.operations.<op>()` call starts a separate one.

Steps inside a chain run strictly in order, each receiving the previous step's result, so a
read can be transformed and written back in one pass:

```python
def rewrite(asset):
    asset.keywords = "updated"
    return asset

with CascadeWrapperBase(environment_variables) as cascade:
    cascade.operations.read(page_a).edit(page_a, rewrite).publish(page_a)
    cascade.operations.read(page_b).then(report)
    cascade.operations.delete(old_page)

    results = cascade.submit_requests()
```

- **Chains run concurrently**, so a batch still costs one round of requests, not one per chain.
  Only the steps *within* a chain are sequential.
- **One result per chain, in the order the chains were built** — `results[0]` belongs to the
  first chain. No more matching responses back to requests by hand.
- **Failures are values, not gaps.** A chain stops at its first failure and that object lands in
  the results: a `CascadeError` when the API rejects a request, or the exception a callback
  raised. Other chains are unaffected. `submit_requests()` returns a `ChainResults` — still a
  list, in that same chain-creation order — with `.success` (results from chains that didn't fail)
  and `.failed` (a `ChainFailure` per failed chain, naming the exact step and its category) added.
  There's no need for an `isinstance(result, CascadeError)` check or a `try/except` around
  `submit_requests()` — `CascadeWrapperBase` owns whether a failure ends the script; see
  "Error handling" below.
- **A callback returning `None`** passes the previous result through, so side-effect callbacks
  (logging, reporting) don't break the chain.
- **`edit()` accepts a callable** as its payload; it is invoked with the previous step's result,
  which is how a transformed asset gets written back.
- Chains are cleared once `submit_requests()` returns, so a callback registered for one batch
  never re-runs in the next.

### Error handling

Failure handling lives in the library, not in your script. `CascadeWrapperBase`'s context
manager owns it end to end:

- No `try/except` around `submit_requests()`, and no `isinstance()` checks in your script —
  read `.success`/`.failed` off the `ChainResults` it returns instead.
- If the batch itself breaks (not an individual chain — e.g. the driver's event loop fails),
  `submit_requests()` raises `CascadeBatchError` rather than returning an empty list.
- At `with`-block exit, with `exit_on_failure=True` (the default), any recorded failure ends the
  script with a non-zero exit code — **code written after the `with` block does not run** in
  that case. A callback exception is re-raised unwrapped (plain traceback); a
  `CascadeError`/network/library-side failure raises `SystemExit(1)` with no extra traceback,
  since it was already logged.
- Pass `exit_on_failure=False` to embed the wrapper in a longer-lived process (e.g. an MCP
  server) — `__exit__` then never raises; read `.success`/`.failed` off the results yourself.
- The logfile's `!ERROR:` line is prefixed `[NETWORK]` for a network-layer failure or
  `[CASCADE-REST-CMS]` for any other library-side failure; a `CascadeError` or a callback
  exception gets no prefix. A batch-level failure takes the prefix that matches its cause.
- Cascade always answers HTTP 200 with a JSON body; a non-200 status (an HTML page from the
  server layer, e.g. `501 Not Implemented`) is reported as a `CascadeError` whose message is the
  status code and reason. The HTML body is never read or logged.
- At exit, the console always prints a tally: `"{failed} failed, {succeeded} succeeded"`,
  cumulative across every `submit_requests()` call in the session, with `": reference log for
  details"` appended only when at least one failure came from the API, the network, or the
  library itself (not from a callback-only failure). The tally is also written to the logfile.

```python
with CascadeWrapperBase(environment_variables) as cascade:
    cascade.operations.read(identifier)
    results = cascade.submit_requests(Asset)
    for asset in results.success:
        ...  # only successful reads
# If anything failed, execution never reaches here — the with block already
# raised (or exited) on the way out.
```

### Logging

`CascadeWrapperBase` accepts an optional second `debug` argument. Leaving it as `None`
(the default) runs in **normal mode**: a minimal console (`[INIT]`/`[LOG]`/`[RUNNING]`/`n/N succeeded`/
`[DONE]`/`[EXIT]`) plus a simple logfile at `./logs/{SERVER}_{timestamp}_{n}.log`. Passing a dict
switches to **debug mode**: a quiet console and a verbose, nested logfile at
`./logs/{SERVER}_debug_{timestamp}_{n}.log` describing every request, response, callback, and error.
Each wrapper gets its own file, even when two are created in the same second.

- **Status lines go to stderr**, not stdout (`[INIT]`, `[LOG]`, `[DONE]`, the tally, `[EXIT]`,
  console `[ERROR]` lines), so stdout stays free for your script's own output.
- **`[LOG]: <path>`** is printed right after `[INIT]`, giving the path of this run's logfile.
- **`log_dir=`** (keyword-only) sets the log directory in normal and debug mode; it is created if
  missing and defaults to `./logs`. If a debug config also sets `log_dir`, the parameter wins.
- **`[EXIT-CODE]:`** is the last line written to the logfile: `0` for a clean run, `1` when the
  script ends with a failure, `1 (callback exception: <Type>)` / `1 (uncaught exception: <Type>)`
  when an exception ends it, or `n/a (exit_on_failure disabled)`. Read the logfile for the tally
  and exit code rather than capturing console output.
- In debug mode with `show_network_headers`, the `Authorization` header is masked to its last four
  characters (`Bearer ****abcd`).

```python
debug_config = {
    "log_dir": "./logs",
    "log_operations": True,
    "log_callbacks": True,
    "log_responses": True,
    "show_payload_data": True,
    "show_network_headers": False,
    "show_error_variables": True,
    "response_line_limit": 8,   # -1 = dump full response body
}

with CascadeWrapperBase(environment_variables, debug=debug_config) as cascade:
    ...
```

#### `[RESULT]` and `[NOTE]` lines

Two fixed line types make the logfile carry what a script *did*, so an agent (or you) can read it
instead of capturing stdout:

- **`[RESULT]: ...`** is written **automatically**, once per successful write operation
  (`create`, `edit`, `delete`, `copy`, `move`, `publish`, `checkIn`, `checkOut`, `siteCopy`,
  `editAccessRights`, `editWorkflowSettings`, `performWorkflowTransition`, `markMessage`,
  `deleteMessage`, `editPreference`), right after that chain's pipeline line. Reads, searches and
  lists write none; failed writes and failed items of a list create/edit write none. A write that
  succeeded stays on record even if a later step of its chain failed.

  ```
  [RESULT]: create page 5f1a0000000000000000000000c90d00 /_dev/testing/testbed2-copy
  [RESULT]: delete page 1a2b0000000000000000000000009f00 succeeded
  [RESULT]: siteCopy succeeded
  ```

  `create` lines are `create <type> <id> [<path>]` (the new asset, as Cascade returned it; `<path>`
  is the site-relative path when the payload gave `parent_folder_path`). Every other write is
  `<operation> [<type> <id> <path>] succeeded`, naming the target (as many of the three as are
  known); an operation with no identifier target is just `<operation> succeeded`. `<id>` is 32
  hex characters and `<path>` starts with `/`.

- **`[NOTE]: <text>`** is written **on purpose** by your script:

  ```python
  from cascade_cms.utils import script_log

  with CascadeWrapperBase(environment_variables) as cascade:
      script_log.note("will delete page 1a2b... /news/old-1")
  ```

  `script_log` is a stateless singleton that routes each note to the run whose `with` block is
  active in the calling context, so wrappers open on different threads never mix notes. Notes must
  be written **inside** the `with` block; outside it the note is dropped and a `RuntimeWarning` is
  emitted. Newlines become spaces (one note = one line) and any occurrence of the API key is
  masked. Thread-pool and async callbacks may call `note()` directly. **Process-pool callbacks
  cannot** (another process, no active run): have them return values and note them in the main
  script.

All keys are required in debug mode — there are no inferred defaults, so you always know what
you opted into.

## Development

```bash
pip install -e ".[dev]"
pytest
```
