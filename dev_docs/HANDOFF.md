# HANDOFF — resumable state after restart

## Where we are

**Phase 3 done: the app runs, and it ships.** The Python service, the Swift
client, the event-stream state machine and a SwiftUI shell (chat, tool timeline,
permission sheet, model picker, session browser) are green, the app spawns its
own service and drives real runs, and `scripts/build_app.sh` produces a signed
119 MB `SoloCoder.app` with the interpreter, its dependencies and the source
inside it. `scripts/package_check.sh` is the gate that says so.

- Branch: `feat/computer-use-macos`, 31 commits ahead of origin — the phase 0–3i
  commits plus the streaming and finished-answer bounds, nothing pushed
- Python suite: **609 passed** (`.venv/bin/python -m pytest tests -q`)
- Swift suite: **419 checks passed** (`swift run agent-checks`), **432** with
  `AGENT_REPO_ROOT` (the app-model checks against a real sidecar); 41 of them are
  the row vocabulary the restyle put in the core, where `agent-checks` can reach
  it; add
  `AGENT_LIVE_MODEL` for a real streamed run with thinking off, a real thinking
  run, and the app model doing two runs on one connection
- Installed at `/Applications/SoloCoder.app`, and `.build/SoloCoder-0.1.0.dmg`
  (62 MB) builds and verifies with `scripts/make_dmg.sh`
- Packaging gate: **6 checks passed** (`scripts/package_check.sh`), **7** with
  `AGENT_LIVE_MODEL` — signature, self-contained load paths, the app's own
  `--self-test` against a relocated read-only copy, imports under a scrubbed
  environment, a real streamed run, parent death, and the seal intact after all
  of it
- The window is on Bionic's visual language (phase 3h): hairline borders, one
  violet accent, neutral surfaces, a 760 pt reading column, and rows that say
  *Worked for 1m 35s ›* / *Reasoned for 12s* / *Ran npm test*
- The sidebar names sessions and deletes them (phase 3i): a title stored once at
  the head of the file, `session.delete`, and the run's claim on a session id
  closing the delete-while-writing race
- An answer is bounded, its scan is gated, and what a publish *draws* is bounded
  too (phase 3i): 12k head + 4k tail with the count of what it dropped shown in
  the line, one markdown re-parse per 4k characters, and at most 60 blocks of 2,000
  characters on screen while it streams. An unbounded, ungated answer wedged the
  app at 100% CPU — 200k streamed characters measured 719 s to fold, and it
  measures 386 ms now. Fixing the fold did not stop the wedge: the live looping run
  still pinned a core, and the cost was the 2,573 views a publish laid out. The
  same bound now covers the answer a run **leaves behind** — the row outlives the
  stream, and every later publish laid out all of it — with the rest of the answer
  behind a "Show all 4,000 blocks and 26,999 characters" button (phase 3i+)
- Working tree: clean as of the send-refusal commit (2026-10-13).
  `.build/app/SoloCoder.app` (120 MB) is two commits behind it — it ships
  `session.delete` and the streaming bound, not the finished-answer bound, the
  remembered scope, the in-window refusal or the prompt a refusal leaves behind;
  `/Applications/SoloCoder.app` is still the phase 3h build and does not know
  `session.delete` at all — installing the new bundle is the step left
- The venv needs the `dev` extra for `tests/test_schemas.py` to collect
  (`fastapi`); a `uv run` sync prunes extras, and it did mid-session

## Phase 3h: the UI, restyled on Bionic

The brief was "the UI is ugly", with **Bionic** (`/Applications/Bionic.app`) as
the reference — not LM Studio, which is what I opened first and quit. Its window
is calmer for four reasons, and the values are read out of the installed bundle,
not guessed at:

1. **Borders are hairlines, not fills** — the foreground at 4%, 7% and 12%
   alpha. A 12%-alpha rule reads as an edge without adding a grey rectangle,
   which is why a screen of its rows does not read as a table.
2. **One accent, sparingly** — violet `#6141FF` light, `#8C80FF` dark; the dark
   value is lighter because a saturated violet on near-black vibrates.
3. **Neutral surfaces** — `#F3F3F3`/`#E8E8E8` light, `#1E1E1E`/`#292929` dark. No
   blue cast, so the accent stays the only coloured thing on screen and keeps its
   meaning.
4. **A reading column** — the transcript is ~760 pt wide and centred, not 1,400
   characters per line.

`AgentAppUI/Theme.swift` is those tokens, every one a dynamic `NSColor` so the
window follows the appearance (and an external display in dark mode) with no
`colorScheme` plumbing in any view.

**The rule that made it testable: a view draws, the core decides.** Every label
and every number on a row is decided in `AgentAppCore`, where `agent-checks` can
reach it — a view's string is a screenshot to verify it. What that added:

- `ToolDisplay.summary(tool:arguments:running:)` — verb then object: *Ran npm
  test*, *Read src/agent.py*, *Searched "TODO" in …*, *Clicked (12, 30)*, and
  *Ran frobnicate* for a tool the app does not know. Paths keep their tail at 46
  characters with the home abbreviated to `~`; quotes are kept at 90.
- `DurationFormat.short` — `12s`, `1m 30s`, `2m`, `1h 5m`, and **nil** under
  half a second: a row that took 0.4s says nothing, because "0s" is a claim about
  a thing that finished instantly.
- `TranscriptLine.at` and `.isActivity`, `Turn` (a prompt, the work, the answers),
  `Turn.activityLabel` — *Working* / *Worked for 1m 35s* — and the reasoning row's
  *Reasoned for 12s*.
- `ToolCallRow.startedAt`/`endedAt`, and `elapsed` reading only those stamps. A
  running row's live count is the view's (`TimelineView(.periodic(from:
  row.startedAt, by: 1))`), not the model's: a duration that depends on when it
  was asked, on a clock the transcript was not stamped with, is not a fact.

**The clock is injected.** `Transcript(terminalTypes:clock:)` takes the clock, so
the checks stamp a scripted run at known instants and assert the words. That
bought four defects the code had, all of them found by writing the expectation
first:

- the turn's duration was measured to the *start* of the last tool call, so a
  22s turn reported 2s — `TranscriptLine.stoppedAt` (a tool row stops at its
  result, not at its request) and `Transcript.finishedAt` → `Turn.runEndedAt`
  fixed the end of the span;
- an orphan tool result — a result with no call, the fallback-append path —
  stamped both ends at "now" and claimed the call took no time. It now answers
  nil, and the row shows no duration;
- `turns` dropped the line that created a headless turn (a run that starts with
  a tool call, no prompt), so that turn's answer count was 0;
- a history loaded from disk stamps every line at one instant, so the first
  `Turn` rebuilt its lines by sorting on `at` and rendered the history shuffled.
  `Turn.lines` is arrival-ordered and `activity`/`answers` are filters of it.

**The one number worth carrying.** The reasoning row's duration is the distance
from its arrival to the next line's — nothing in the protocol says "the model
stopped thinking". The first answer was `Turn.nextArrival(after:)`, a scan from
the line to its successor: O(lines) per row, O(lines²) for a turn whose
disclosure is open, and **10.532 ms** measured over the 500 activity rows of a
1,000-line turn — a sixth of the 60 ms publish cadence, on every publish, growing
with the square. `Turn.durations` answers the same question from one pass over
the lines, keyed by line id, read once per turn render: **0.1231 ms**, 85×
cheaper and linear. The lesson is the shape: a per-row accessor that walks the
turn is invisible at 20 rows and is the cost of the app at 1,000, and the row
cannot see it — it asks for one value and gets a loop. The view reads the
dictionary once in the section and hands each row a `TimeInterval`, because a row
that carried the dictionary would compare it in its `.equatable()` every publish.

**Verified on screen, not only in checks.** `ui-preview` (no service, no model)
for the populated timeline, the empty state, the permission sheet, and the
composer draft; then a real run in the real app against the real service, which
is the only way to see a live row: *Working for 1m 11s* → *Worked for 1m 35s ›*,
*Reasoning…* → *Reasoned for 12s*, *Ran ls -la … 1s*, the stop button replacing
send while the run is in flight, the prompt as the header title, the answer as
markdown.

The traps this phase hit are in *Things to know*: the `onSubmit` draft, the
nil-proposal frame, the black capture of an occluded window, and `noService` from
the package directory.

Closed by phase 3i, below: the session browser's titles and delete.

## Phase 3i: the session browser, and the answer that does not end

**The protocol change the sidebar needed.** `session.list` rows carry a `title`,
and `session.delete` exists. The protocol version stays **1**: a field added to a
response and a method added to the table are additive, and the regenerated
fixtures (`tests/service/fixtures/protocol.json`, via
`scripts/gen_protocol_fixtures.py`) pin that.

**The title is derived once and stored.** `derive_title` takes the first user
message's first text block, collapses its whitespace, and cuts it at
`TITLE_MAX = 200` — a payload bound, so a `session.list` frame stays one frame.
`sessions.save` reads the title already in the target file (`stored_title`) and
hands it to `Session.save(path, title=…)`, which writes it as the file's **first
key**. Deriving it once is the whole point: a session that compacts its history
keeps the name its first prompt gave it, and `session_title` (stored, else
derived, bounded on the way out) cannot rename a conversation under the user. A
session that opens with a screenshot has no title and shows its id — which is at
least the string `--session` takes. `Session` owns no title; the writer decides
it, and a test pins that `save` puts it at the head.

**Delete never parses.** A damaged session file is still removable — the row in
the sidebar is the way to delete the thing that will not open — and a missing
file answers `False`, which the app shows as "*X* was already gone" rather than
as a success.

**The claim that closes the race.** `AgentService._live: dict[str, _Run | None]`
claims a session id **before** `await self._build_provider(ref)` — the await
where a second `run.start` can land — and refuses a claimed id with
`session_busy` (`-33010`). The release happens after `sessions.save` with no
await between, so a session is never deleted while a run is writing it. The app
gets there first for its own run (`SessionDeletion.isOffered`: a row you are
watching does not offer to remove the thing writing it); the server's refusal
stays the backstop for a run started on the CLI, which the app cannot see.

**Verified in the live app**, which is the only way to see the whole chain: a run
whose session file begins `"title": "Reply with exactly the word TITLEPROBE and
nothing else."`; a right-click → *Delete Session* → the confirmation naming the
session **by that title** → the file gone from `~/.openagent/sessions/`, the row
gone from the sidebar, the timeline back to *No run yet*, and the notice
*deleted Reply with exactly the word TITLEPROBE and nothing else.*

**Then the app died of an answer that never ends.** A run on a resumed session,
sent from the composer: the window stopped answering, `sample` showed the main
thread pinned inside SwiftUI's attribute graph with `Markdown.blocks` under it,
and RSS climbed **19 MB a second** (331 MB → 1.68 GB) until the process was
killed. Reproduced from a clean launch in 40 seconds, and it kept spinning after
Cancel — cancelling stops the run, it does not take the text back out.

The cause is the one place the app deliberately kept a bound away. The thinking
stream is capped (`reasoningKeep`, a tail of 8k) because it is the longest text
in the app and the least valuable. The answer was uncapped because it is the
product, shown in full and copied out — right about an answer a model finishes,
and the answers measured here are short. A quantized local model **loops**, and
the answer becomes the longest text in the app, and two per-delta costs are
O(n) in it: the markdown re-parse `appendAnswerDelta` runs whenever a newline
lands, over the whole answer, and the publish that folds the next delta copies
the transcript and lays the answer out again.

The harness measured it, and the measurement needed a probe the harness did not
have: `foldAnswer` streams one long line, and the re-parse only fires on a
newline, so it measured the append and never reached the parse. With a newline
per delta (`swift run ui-cost`, section 2b):

| answer | fold cost, total | last 1000 deltas |
|---|---|---|
| 20,000 chars | 7.2 s | 1.16 s |
| 80,000 chars | 114 s | 21.7 s |
| 200,000 chars | **719 s** | **56.8 s** |

Quadratic, in the phase the user is watching, at 57 ms per delta — one delta
every 60 ms of stream is a full core.

**The fix is the same bound, at both ends.** `Transcript.answerKeepHead = 12_000`
and `answerKeepTail = 4_000`, trimmed at `answerTrimAt = 32_000` (the trigger is
above the target, so the trim is not paid per delta, as with `reasoningKeep`).
The head, because an answer is read from the top; the tail, because it is what is
arriving and the user watches the stream move. Between them the line says what it
dropped, in the words a tool result already uses — *… truncated: showing the
first 12,000 and last 4,000 of 400,000 characters*. Nothing is lost: the service
saves the full text and `--resume` reads it back.

**The cap alone was not enough, and the harness said so.** Re-measured with the
cap in, 200,000 characters went from 719 s to 162 s — 6.5 ms per delta, a dropped
frame every time the model ends a line. The cap bounds the string; the scan is
O(n) *in that bounded string, once per newline*, so the cost is still linear in
the deltas, and a looping model's deltas never stop. (The length test is not the
cost: `String.utf8.count` is O(1) on a Swift String, ASCII or not — measured on
200k characters of each, ~0 ms.)

So the scan is gated: free below `answerScanFreely = 2_000`, where a scan of a
short answer costs nothing and a committed block should render as soon as it
commits, and once per `answerReparseEvery = 4_000` characters above it.

The gate opens a seam: the blocks stop at the last scan and the answer has grown
since. The view closed that seam by asking `Markdown.split(text).tail` for its
half — the **newest** boundary — so the text between the two boundaries was drawn
by neither and disappeared mid-stream. The boundary therefore lives on the line
(`TranscriptLine.tailText`), the reducer moves it at each scan, and the view draws
`blocks` and `tailText` and parses nothing at all.

| answer | uncapped | capped | capped + gated |
|---|---|---|---|
| 20,000 chars | 7.2 s | 7.3 s | 87 ms |
| 80,000 chars | 114 s | 59.5 s | 194 ms |
| 200,000 chars | **719 s** | 162 s | **386 ms** |

Nine checks in `TranscriptChecks` hold it: the bound, the head, the tail, the
marker, the block count that follows from the bound, and four on the seam — every
paragraph of a 300-paragraph streamed answer on the screen exactly once, the tail
holding the newest text and what committed since the last scan, the blocks
stopping short of it.

**The reducer was fixed and the app still wedged, and only the live run said so.**
With the display awake: a normal 21,997-character, 320-newline answer streamed
clean — 29% CPU average, 93.5% peak, RSS 175 MB → 451 MB and flat after that,
rendered as markdown, title stored. Then the looping prompt that had wedged
before, with the cap and the gate both in: CPU pinned at 100%, RSS 178 MB → 782 MB
in two minutes, 6.3 MB/s. `sample` put the main thread in
`GraphHost.runTransaction` → `AG::Subgraph::update` → `ViewLayoutEngine.sizeThatFits`,
`NSConstraintBasedLayoutInternal`, `NSControl setFont:`,
`SelectionOverlay.updateNSView` — **no frame from `AgentAppCore` or `AgentAppUI`
in the sample at all.** The 386 ms above is real, and it is not the cost.

The unit was wrong. A publish lays the streaming answer out as **views**: one per
block, and one text view per block however long that block is. Measured
(`swift run ui-cost`, section 2c), a looping answer with blank lines leaves 2,573
blocks; one without blank lines leaves a single paragraph of 18,005 characters; a
bulleted loop leaves one `.bullets` block of 11,245 characters. All of it laid out,
whole, every 60 ms, for as long as the run does not stop.

So the **view** is bounded, on both axes, in the core: `answerBlocksShown = 60`
(about three screens of answer — the reading position) and
`answerBlockCharsShown = 2_000` (about one), applied by
`TranscriptLine.streamingBlocks`, which returns the blocks to draw and counts what
the bound left out (`hiddenBlocks`, `cutChars`) so the row can say *… 2,513 blocks
above are not shown*. The last blocks, not the first, because a streaming answer
is read where the text is arriving. The stored answer is untouched — the cap keeps
12k head and 4k tail, `blocks` is never trimmed, and the tail is never cut. A
**finished** answer is drawn whole: one layout of the product is not this defect,
and a bounded answer in the pasteboard is a bounded answer.

| loop shape, 24k streamed | what a publish laid out | now |
|---|---|---|
| no blank lines | 1 block of 18,005 chars | 1 block of 2,000 |
| blank lines | 2,573 blocks of 12,865 chars | 60 blocks of 300 |
| bullets, no blank lines | 1 block of 11,245 chars | 1 block of 2,000 |

Twenty checks in `TranscriptChecks` cover all of it, eleven of them this last
bound: the block count, the hidden count being the difference and not a guess, the
newest committed block surviving the bound, the top dropped, the tail untouched,
the stored blocks intact, one paragraph of 18k drawn as 2,000 characters with the
cut counted, and a finished line drawing nothing from the bound because it is drawn
whole.

**The live re-run of the loop is still open, and it is open on a credential, not
on a display.** The app streamed a normal 21,997-character answer clean with the
bound in, and the harness says the drawing is bounded; the looping prompt that
wedged needs a model the app can reach, and none is reachable right now:
`lmstudio/qwen3-coder-32b` (the router's choice) is not serving, `ollama` and
`mlx` are not running, and the app's sidecar answers
`notAuthenticated (-33002)` for `gemini` and for `anthropic_oauth` — the
subscription auth this CLI uses is not in the app's environment. The `lan`
endpoint answers 401 (reachable, key needed) and is not in the app's model menu
at all, because discovery of a `requires_key` endpoint with no key lists nothing.
So: start LM Studio, or give the app a credential, and the run is two clicks.

**A refused run leaves no row in the timeline, and that is a defect this run
found.** Send a prompt with a model that has no credential: the app shows
*The service said no — notAuthenticated (-33002)* as a modal, and dismissing it
leaves the two prompts and nothing else. The app model does record the failure
(`AppModelChecks`: "state after an unreachable run: Failed: Connection error."),
and the transcript has a `.messageFailed` kind, so the row exists in the
vocabulary; the view draws the modal and not the row. A failure the user can
dismiss is a failure that never happened — the session file has no trace of it
either.

## Phase 3i+: the credential store, and the model table that went stale

The live loop test needed a model, so the app signed in to ChatGPT. That sign-in
took the whole session, and it is worth the detail because the store was the
defect, twice.

**A keychain write that fails must not leave anything behind.** `security
add-generic-password` reads the secret from stdin a line at a time and can exit
0 having stored a prefix of it. `_keychain_set` already noticed — it reads back
and compares, and the comparison failed, so `set_secret` fell back to the file
backend, which wrote the whole 4,281-character payload. What nothing noticed is
the item the CLI left in the keychain: `get_secret` reads the keychain **first**,
so the truncated prefix shadowed the intact copy on every later read, and
`auth status` answered "Ignoring corrupt credential entry … not signed in" about
a token sitting on disk. The only way out was `auth logout`. Three changes:
`_keychain_set` deletes the item when the read-back does not match;
`SecretManager.quarantine_secret` drops the entry `get_secret` would have
returned and hands back the copy the other backend holds, left in place; and
`AuthBroker.load_credentials` calls it when a payload will not parse. The repair
runs on read, so the next `auth status` on the same machine printed
`✓ ChatGPT Plus / Pro  signed in  385c0a82-…` with no re-sign-in.

**The plan claim was read under the wrong name, and the test agreed with the
code.** OpenAI spells it `chatgpt_plan_type`; `flow.py` read `plan_type`, and
`test_oauth_flow` asserted `plan_type` — so the suite was green and every real
sign-in stored `plan_type=None`. A test that repeats the code's spelling of a
vendor field checks the spelling, not the field.

**The Codex backend is closed to this account, and the wording proves nothing
about model ids.** With a valid token, an account id, and plan `plus`, every id
— `gpt-5-codex` (the endpoint's own default), `gpt-6-x`, `gpt-6-codex`,
`gpt-5.1-codex-max`, `gpt-5.1-codex-mini` — returns the identical
`400 {"detail": "The 'X' model is not supported when using Codex with a ChatGPT
account."}`, and `GET /models` returns a 403 HTML page. Sending the account id as
`ChatGPT-Account-Id` instead of `OpenAI-Organization`, or not sending it, changes
nothing. So the message is generic: it is not evidence that any id is right or
obsolete, and no id added to the catalog here could be checked. The subscription
path is not usable from this machine; `lan/swift-1.5-iq3_s` is.

**The built-in model table is hand-curated, offline, and it went stale.** It is
not fetched from anywhere, so it is a list of what the repo author knew when the
file was written: `claude-opus-4-1`, `gpt-4o`, `gemini-2.0-flash`. The Anthropic
rows now name the Claude 5 family, the endpoint defaults moved with them, and
prices are left unset where they are not known — the CLI renders an unknown price
as the cost kind, and a made-up number is worse than none.

**A stored default must be one of the rows.** `~/.openagent/config.json` still
says `default_model: anthropic_oauth/claude-sonnet-4-5`. The app adopts
`default_model` when it has no selection of its own, and `run.start` for a model
the catalog does not contain answers `MODEL_UNAVAILABLE: unknown model` — so
refreshing the table would have broken the app's default. `_model_list` now
reports `default_model` only when it is among the rows it returns.

**The picker is as fresh as the last `model.list`.** The app calls
`models(discover: true)`, and the sidecar's discovery does find
`lan/swift-1.5-iq3_s` (200, one id) with the stored key. The menu showed nine
rows and no `lan` row because it was the snapshot from before the LAN server was
reachable; pressing Refresh re-ran discovery and the row appeared as
`lan/swift-1.5-iq3_s`. A model server that comes up after the sidecar starts is
invisible until something asks again.

Gates: **609** Python tests.

## Phase 3i+: the answer a run leaves behind

The live loop test — the prompt that writes `apple` on its own line 4,000 times —
found the case phase 3i's three bounds did not cover: not the answer while it
streams, but the answer a finished run **leaves behind**.

**The bound stopped at the stream, and the row outlives the stream.** The view
applied `streamingBlocks` only while the line was streaming; the rule written in
the comment was "the bound is for the stream, not the product". A completed answer
is the product, and it is in the viewport: `message_completed` replaces the line,
the row stays on screen, and every publish of every *later* run lays out every
block of it. The looping run leaves 2,573 blocks. Measured on the next run in the
same session: main thread pinned at ~100% CPU, RSS 235 MB → 800 MB in 90 s,
`sample` all inside AttributeGraph (`AG::Graph::propagate_dirty`,
`AG::Graph::UpdateStack::update`, `AG::Subgraph::update`, `DisplayList`), the AX
walk timing out, and the Stop click never delivered — 45 s after Cancel the run
still said "Starting…". The unit that costs is the **count of views in the
viewport**, not characters: the timeline is a `LazyVStack`, so it bounds rows,
and `BlocksView` is a non-lazy `VStack` of one `Text` per block, so it bounds
nothing.

**The same bound, on a finished line, and the parse moved to the reducer.**
`streamingBlocks` is now `shownBlocks` and the view uses it for both states: the
last `answerBlocksShown` (60) blocks, each capped at `answerBlockCharsShown`
(2,000) characters, plus the last 2,000 characters of the tail. The rest is behind
a button. The markdown parse that produced `blocks` used to run in the view, so it
ran on every render of a row that never changes again; `TranscriptLine.parseAnswer()`
is called by the reducer at `message_completed` and by `addAssistantMessage` on a
history load — once per answer. `blocks` is no longer documented as "empty for a
finished line", because it is not: it is the answer's structure, kept whole, and
`shownBlocks` is what the view may lay out. The stored answer is never trimmed
further than the 12k+4k cap phase 3i set.

**The row names what it left out, on both axes.** `ShownBlocks.hiddenSummary`
("… 3,940 blocks and 24,999 characters above are not shown") and
`revealLabel` ("Show all 4,000 blocks and 26,999 characters") are computed in the
core, because a check asserts them. Blocks and characters are counted apart
because they are cut apart, and a run-on answer has **no blocks at all**: 20,000
characters of `apple` with no blank line is one tail and zero blocks, and a row
that said "Show all 0 blocks" about it — the first live screenshot said exactly
that — is reading from the wrong axis.

**Measured** (`swift run ui-cost`, new section 2d; the parse column is the whole
answer parsed once):

| completed answer | delta | the parse | drawn | behind "Show all" |
|---|---|---|---|---|
| 3 paragraphs, 37 chars | 0.0 ms | 0.1 ms | 2 of 2 blocks, 22 of 22 chars | nothing |
| 400 paragraphs, 5.9k chars | 1.2 ms | 1.3 ms | 60 of 399 blocks, 780 of 5,077 chars | 339 blocks |
| 4,000 paragraphs, 63k chars | 3.6 ms | 13.1 ms | 60 of 3,999 blocks, 840 of 54,876 chars | 3,939 blocks |
| 4,000 paragraphs + run-on tail, 90k chars | 3.6 ms | 13.6 ms | 60 of 4,000 blocks, 840 of 54,890 chars | 3,940 blocks, 24,999 tail chars |

A short answer is drawn whole — the bound only speaks when it cuts — and a long
one draws tens of views instead of thousands.

**Live**, on the rebuilt app, `lan/swift-1.5-iq3_s`, permission mode `ask`:

| | before | after |
|---|---|---|
| open the session holding the looping answer | ~100% CPU, 235 → 800 MB, the AX walk times out | **0.0% CPU, 126 MB** |
| a streamed answer with a table, headings and numbered lists | a pinned core | 0-48% CPU, RSS 146 → 167 MB over 22 s |
| Stop pressed mid-stream | never delivered — "Starting…" 45 s later | the run closes; **0.0% CPU and a flat 166 MB two seconds later**, composer back to idle |

The looping prompt does not loop any more: asked to "reply with exactly the word
apple, 4,000 times, no tools", the model answers with a repo-orientation report
and asks for a task number. The report is the better test — a markdown table,
headings, two numbered lists, code spans — because that is the shape a real answer
has, and the row draws it bounded.

**The relaunched app had no scope, so the first live loop test measured nothing.**
The prompt was sent, the service refused the run — "choose a working directory —
it is the scope the agent works in" — and the app sat at 0.0% CPU with a flat
141 MB: a clean measurement of a run that never started. `AppModel.workingDir` was
in-memory only, so *every* launch began with that modal, and the first prompt of
every session was the one refused. It is stored when set and restored at launch,
and `AppModel.restorableWorkingDir` drops a stored path that is not a directory —
a folder deleted over the weekend is not a scope, and a status bar naming a folder
that is gone is worse than one that asks for a folder. Driving the panel from the
command line is `cmd+shift+g`, the path, `cmd+Return` — and then Choose, with the
trap below. For a test that only needs *a* scope, `defaults write solocoder
openagent.workingDir <path>` and relaunch is the honest way to set it.

Traps this phase added:

- **A tail is long only when the deltas carry no newline.** `Markdown.split` cuts
  the tail at the *last blank line*, and text ending in `"\n"` leaves a trailing
  empty element that reads as blank — so a run-on answer streamed as
  `"apple\n\n"` deltas commits as one block and leaves the tail empty. The
  first tail-bound check asserted `tailHidden > 0` on that shape and failed for
  the right reason: there is no tail to bound. The check streams 3,000 words with
  no newline, which is the shape that has one.
- **Which half holds the end of the answer.** The last paragraph is in the blocks
  (the split cut before it) and the run-on ending is the tail, so "the drawn
  blocks reach the end" and "the tail is the end" are two checks, not one.
- **A JSON payload built by interpolation needs `jsonLiteral`.** A raw newline
  inside `#"…"#` interpolated into `output_text` is a fatal
  `Unescaped control character '0xa'` in the checks and in `ui-cost`; encode the
  string with `JSONSerialization` and drop the surrounding `["…"]`.
- **`pgrep -f` finds the shell, not the app.** The launcher's command line
  contains the binary path and its pid is lower, so `pgrep -f …/debug/solocoder |
  head -1` samples `/bin/zsh`. Use `pgrep -x solocoder`.
- **Setting `AXValue` on the composer does not drive the SwiftUI binding** — the
  field shows the text and the send button stays disabled. Real keystrokes work.
- **The open panel chooses the *highlighted row*, not the folder you navigated
  into.** Typing the repo root in the `cmd+shift+g` field and clicking Choose set
  the scope to `__pycache__`, then to `frontend` — the row the arrow keys had
  landed on. Clicking in the list to "deselect" selects a row; there is no click
  that clears the selection. And the Return that closes the panel lands in the
  composer, so the typed path went out as a prompt. `cmd+Return` in the go-to
  field navigates without a second Return; Choose with nothing highlighted takes
  the folder.

**A refusal stopped wearing a modal.** The system `.alert` — a card with a bold
title "The service said no" and a full-width blue OK — blocks the composer, and
this app's refusals are routine: no scope, an unknown model, a service that died.
Bionic has no modal for a routine. `BannerCard` is a hairline-bordered card at the
top of the window, amber icon, violet "Dismiss", the window left usable. Its first
text was **"notConnected"**: `AppModel.describe` rendered `"\(error)"`, the enum's
mirror name. It now reads `LocalizedError.errorDescription` — the sentence every
client error already carries — and keeps the mirror name for an error that chose
no sentence, because a plain `NSError`'s `localizedDescription`
("The operation couldn't be completed. (…)") is worse, not better. Its first
live test found the other half of the defect: the card said "choose a working
directory", the timeline stayed at "No run yet", and the words were gone from the
composer, so there was no record of what was refused. `send` now returns the
reason and the composer clears only when the prompt went — a run the *service*
refuses already leaves a row (`failLocally`); a send the composer blocks leaves
the prompt in the field, which is the record.

Open defects, all seen on screen:

- **Cancel is delivered only when the main thread is free.** The bound removes the
  pin that made the Stop click undeliverable; it does not remove the class. A run
  that wedges the main thread in some other way swallows the click again.
- **The picker is as fresh as the last `model.list`** (previous section), and the
  launch takes the sidecar's `default_model` and `Ask` — the scope is remembered
  now, the rest of the session's settings are not.

Gates: **419 checks** (`swift run agent-checks`), **432** with `AGENT_REPO_ROOT`.

## Phase 3f: a `.app`, and what the packaging gate found

`scripts/build_app.sh` produces `SoloCoder.app` (119 MB): the Swift binary, and
inside it a python-build-standalone interpreter with the runtime dependencies in
its own `site-packages`, next to the project's source. Signing is ad-hoc,
innermost-first, no hardened runtime, no sandbox.

Not PyInstaller, not a venv, and the reason is relocation. Gatekeeper
translocation runs a quarantined app from
`/private/var/folder/…/AppTranslocation/<uuid>/d/`, so the payload must work as a
directory tree that moved. PBS is: `sys.prefix` follows the executable. A venv is
not: `pyvenv.cfg` pins `home = <absolute path>` outside the bundle, and its
`bin/python` is a symlink. PyInstaller onefile unpacks ~150 MB per launch and
breaks pyobjc's Objective-C classes.

`scripts/package_check.sh` is the gate, and the app does the middle of it:
`SoloCoder --self-test` runs the app's own startup headless — resolve, spawn,
connect, ping, `tool.list`, `auth.status`, `model.list`, stop, socket gone — and
the script runs it against a **relocated, read-only copy** of the bundle with a
scrubbed environment. A shell script poking the interpreter by hand would pass on
a bundle the app cannot launch.

`--self-test-run <model>` adds one step, off by default because it needs a
reachable model server: a real streamed run, so the gate proves the shipped
service can *answer* and not only be reached. Against `lan/swift-1.5-iq3_s` it
returns `PACK` — four characters, from a bundle that cannot write to itself.

Six checks, and the first run of them found four defects, all invisible to
`swift build` and `pytest`:

- **SIGTERM had no handler.** The app's `stop()` sends it, and the default action
  terminates the process where it stands: the socket stays bound (the next
  launch's `start()` finds a live listener and refuses), the MCP subprocesses are
  orphaned, and `handle_serve`'s `finally` — the only caller of `stop()` — never
  runs. Fixing that exposed a second bug: the signal handler and the `finally`
  both call `stop()`, and the second awaited `wait_closed()` on a field the first
  had already cleared. `stop()` now takes the server out of the field before its
  first await. `tests/service/test_watchdog.py::test_SIGTERM_shuts_the_service_down_gracefully`
  covers both, with stdin on `/dev/null` so nothing but the handler can save it.
- **The gate broke the seal of the bundle it was checking.** Its dependency probe
  imported the whole set without `-B`, writing `__pycache__` into the bundle's
  `site-packages` — a resource added after signing, so `codesign --verify` failed
  on the next run. A check that mutates the thing it verifies has to run against
  the read-only copy. The gate now ends by re-verifying the build tree, which is
  what catches this class.
- **`compileall` writes into the interpreter's stdlib.** It imports stdlib
  modules, and an interpreter without `-B` writes their byte code beside the
  source. `-B` is not an option there — it blocks `compileall`'s own writes — so
  the build strips every `__pycache__` under the interpreter tree after compiling
  the app's, and the seal covers what ships.
- **A Finder-launched app is not a terminal launch.** It gets
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin` and cwd `/`, so the bash tool cannot find
  a Homebrew `git`, `rg`, `node` or `uv`, and an MCP server configured as
  `npx`/`uvx` fails at `exec`. The environment is now curated rather than
  inherited, with the PATH taken from a login shell (2 s budget, then the
  LaunchServices default), and the cwd is the home directory, not the bundle.

Two more that only a bundle shows:

- **`-I` drops the script's directory *and* the cwd from `sys.path`.** The bundle
  resolves `import openagent` because `cli_coder.py` inserts its own parent — not
  because of where it sits. A `-c "import openagent"` probe fails on a bundle
  that launches fine; that is a fact about `-I`, not a defect.
- **`FD_CLOEXEC` on the parent-death pipe's write end.** The service spawns its
  own children, and a forked child inherits the write end. If it does, the read
  end never sees EOF when the app dies — a grandchild is holding it — and the
  service that was supposed to shut itself down serves forever, in a socket
  directory named after a dead app.

The parent-death path is verified end to end: `kill -9` the app, and the sidecar
is gone inside a second. `scripts/package_check.sh` opens a real window for ~3 s
to prove it, which is the one thing about that script worth knowing before
running it.

Open, in order of who will hit them: Developer ID signing and notarization (the
entitlements file is written for the hardened runtime, and is inert under
ad-hoc); `SETEXPGID` + `killpg` so a killed service takes its grandchildren;
TCC grants keyed to the ad-hoc cdhash, which changes every rebuild, so
`tccutil reset All dev.solocoder.app` after a build.

## Phase 3g: on the machine, and two things the gate could not see

The bundle passed its gate and then met a user. Both reports were about the
window, and neither was visible to `swift build`, `pytest` or
`scripts/package_check.sh`.

**Installed, and as a dmg.** `/Applications/SoloCoder.app`, copied with `ditto`
(keeps the interpreter's symlinks and the signature; `codesign --verify --deep
--strict` and `--self-test` both pass from that path). `scripts/make_dmg.sh`
builds `.build/SoloCoder-0.1.0.dmg` — 62 MB of 119, UDZO, the app beside an
`Applications` symlink, image signed, then verified by mounting it **read-only**
and running `--self-test` from inside the mount. A mounted dmg is the cheap
stand-in for translocation, so the dmg's check is the same shape the packaging
gate already proved. Three things about it: the copy in `/Applications` is a
snapshot, so a rebuild needs `build_app.sh --binary` and a `ditto`; there is no
`CFBundleIconFile`, so the Dock tile is the generic one; and the image is for
this machine — a file built here carries no `com.apple.quarantine`, and the same
file emailed to anyone arrives with it, which an ad-hoc signature cannot survive.
That last one is the Developer ID item above, not a defect in the script.

**Return.** The composer had Return writing a newline and ⌘Return sending,
written down with a real reason: prompts are multi-line, and Return-as-send costs
a paragraph mid-thought. The reasoning is right about the cost and wrong about
the key — every chat box on the machine sends on Return, so a Return that quietly
inserts a newline is reported as a Send that does not work. Now **↵ sends, ⇧↵
writes the newline** (⌥↵ too; ⌘↵ kept for anyone who learned it). Not verifiable
headless: posting key events needs an Accessibility grant for the posting
process. ↵-sends is the code path ⌘Return already used, so it is proven;
⇧↵-newline is the branch only a thumb can check, and its failure mode is a
doubled blank line (SwiftUI's vertical field inserts its own newline for some
modifier-Returns, and `submit()` appends one).

**Markdown.** The report was "it didn't render markdown", and that is two
different things. The parser is not one of them: a prompt forcing every construct
through `lan/swift-1.5-iq3_s` produced textbook markdown and
`Markdown.blocks` classified all of it.

- *The bug.* `message_completed` was the only event that closed an assistant
  line, so a run that ends without one — Cancel pressed mid-answer, a stream that
  drops, a run-level failure with no message-level one — left the answer
  `streaming` for the rest of the session, which is the user reading `## Plan`
  and `**bold**` in a run that finished two minutes ago. Every terminal event now
  closes the streaming lines. Deliberate side effect: a cancelled run's thinking
  collapses into its disclosure instead of staying expanded, and one check that
  asserted "it stays open, because nothing closed it" was updated to say why
  36,000 characters of thinking have no business being open in a finished run.
- *The design.* A streaming answer was plain text on purpose: parsing
  `Markdown.blocks` per publish is O(n²) in an answer that grows per publish. It
  is now markdown up to its last blank line, with the block in progress plain —
  a `- it` can become a rule or a heading on the next character, so the tail
  cannot be committed early. `Markdown.split` says where that boundary is, and is
  fence-aware: a blank line inside an open fence is not a boundary, because the
  fence swallows everything after it. The parse runs in the reducer, once per
  finished block, cached on the line (`TranscriptLine.blocks`), so the view parses
  nothing per publish.

The O(n²) the old comment was guarding is measured, not assumed. Per-delta split,
ungated, debug build, `swift run ui-cost`: 5.5 ms / 470 ms / **7.4 s** of
main-actor work for 2k / 20k / 80k-character answers. The boundary can only move
when a newline arrives, or when the text ended on a blank line the next delta
fills in — a trailing blank line stops being blank the moment a character lands
on it. Gated to that: **0.3 ms / 3.3 ms / 19.6 ms**.

Swift suite: 288 → **319 checks**. New: the split rules, block-boundary parsing
from deltas, the cancelled-run close, and one live assertion that a real streamed
answer has parsed blocks *before* the run ends.

## Phase 3b+: the app on screen, and why the stream felt slow

The app was invisible: a SwiftPM executable launched from a shell starts
`.prohibited` — no Dock tile, no menu bar, window behind everything.
`setActivationPolicy(.regular)` in `applicationDidFinishLaunching` fixes it. The
enum is `0=regular, 1=accessory, 2=prohibited`, the reverse of what the numbers
suggest.

The streamed answer felt slow. Measured, in order of size:

| What | Cost |
|---|---|
| Model first token (`lan/swift-1.5-iq3_s`) | 18–97 ms |
| Model generation | 8–60 tok/s |
| One turn, real request shape | 1.4–4.4 s |
| Real request shape | 8,461-char system prompt + 19 tool schemas ≈ 18k chars, re-sent every turn |
| Folding one event into `Transcript` | 6.5 µs |
| One published write | a SwiftUI invalidation of every view observing `AppModel` |

The model streams ~1.5 characters per chunk, so a 2k-character answer is ~1300
deltas, and each one invalidated the whole view graph — toolbar, model picker,
session menu, every visible line — while a selectable streaming `Text` rebuilt
its text-kit view per character. Fixed by buffering deltas and publishing on a
60 ms cadence (200 deltas → 4 publishes, checked), making each row its own
`Equatable` view, and turning text selection off mid-stream
(`TranscriptLine.streaming`). `swift run ui-cost` reproduces the fold numbers.

The sidecar no longer outlives the app. Four orphaned services had accumulated
from killed app instances: `willTerminateNotification` does not fire on `kill`,
and nothing fires on SIGKILL. `Sidecar` now hands the service a pipe on stdin and
`AgentService.install_stdin_watchdog()` exits on EOF — the one parent-death
signal that cannot be missed. Verified: `kill -9` the app, the sidecar exits and
unlinks its socket. `/dev/null` stdin is not a parent (checked), so a detached
service still starts.

## Phase 3c+: the ten seconds of nothing

"TG feels slow" was not generation speed. Measured on `swift-1.5-iq3_s` over the
LAN server: first reasoning token at 55–311 ms, **first answer character at
9.5–10.5 s**, ~7,500 chars of private reasoning before ~1,300 chars of answer,
and 150–330 tok/s once the answer starts — matching the server's own benchmark.
The provider read only `delta.content`, so the app showed a frozen spinner for
the whole thinking phase. One default run produced **686,439 characters of
reasoning and no answer at all**.

- `ProviderReasoningDelta` → runtime `reasoning_delta` → protocol event →
  `ReasoningLine` in the timeline: open while streaming, collapsed when the
  answer starts, never joined to the answer, never sent back to the model.
- `run.start` takes `reasoning_effort` (`none|minimal|low|medium|high`), per-run,
  applied to every turn; the toolbar exposes it. `none` answers in 1.1 s.
- The drift test caught the new event being registered in the protocol but
  missing from the `RuntimeEvent` union; the Swift checks caught it missing from
  `Wire.eventTypes` and from the handshake comparison. Each of those is a
  cross-language contract check doing its job.

Publishes in a real tool loop run ~50/s, not the 17/s the 60 ms cadence implies:
every non-delta event flushes the delta buffer to preserve order, and a tool loop
is mostly non-delta events. The synthetic 200-deltas-→-4-publishes number is the
no-interleaving case.

## Phase 3c+: the thinking path was the slow one

"Token generation gets very slow once it's in thinking" was not the model. The
60 ms buffer covered `message_delta` and not `reasoning_delta`, so every thinking
token was a published write — and the thinking stream is the long one: ~7,500
chars before ~1,300 chars of answer, one run at 686,439 chars. The answer-path fix
covered the short half of the run, which is why a run felt fast while answering
and slow while thinking. Three costs, all on that path:

| What | Before | After |
|---|---|---|
| 400 reasoning deltas | 400 publishes | 1 publish |
| Folding a 480k-char thinking stream | 267 ms, per-delta cost 0.4 µs → 5.8 µs | 49 ms, 0.4 µs → 0.5 µs |
| Streaming `Text` of the whole thinking | O(n) layout per publish, O(n²) per run | a 1,200-char tail, constant |

- `AppModel.isDelta` covers both delta kinds: one buffer, one cadence.
- `Transcript` keeps a **tail** of the thinking (`reasoningKeep` 8k, trimmed at
  16k) instead of the whole stream. The thinking is never sent back to the model,
  never part of the answer, never saved as history, and the view shows its tail;
  the answer stays uncapped, because it is the product.
- The copy-on-write trap, measured: appending to a string read out of an array
  element copies the whole string, because the element still shares it. The
  take-out-and-put-back idiom that fixes this under `-O` is **worse** in a debug
  build — 253 ms against 7 ms for 80k appends — because the `let` keeps the string
  shared. Bounding the string is the fix that holds in both builds.
- Checks: 400 reasoning deltas → 1 publish; a 36k-char thinking stream is bounded
  (12,870 chars kept) and keeps its **end**, not its beginning; the live app check
  now runs a *thinking* run on a real model. It did not before — the live run
  asked for `reasoning_effort: none`, so the suite measured the fast half of a
  reasoning model and stayed green while the slow half got slow.

### What a long session costs, measured (`swift run ui-cost`, release)

The four places a value-type transcript pays, on a 401-line session:

| Where | Cost | Shape |
|---|---|---|
| folding the session | 44 ms (5 µs per event) | linear, once, off the main thread |
| one delta at the end | 6 µs | **flat** in timeline length — applied in place |
| appending the answer | 0.1 → 1.4 µs per delta at 80k chars | O(n) per delta, 7.4 ms for the run: uncapped on purpose, and cheap |
| one publish | 7 µs at 401 lines, 68 µs at 4001 | the COW copy of the lines array, linear in line count, ~0.1% of the 60 ms cadence |
| the `.equatable()` row skip | O(1) per row | an unchanged row's string shares its buffer; `==` starts with an identity check |

Nothing here is on the table. The publish — a view-graph re-render — is what a
long session costs, which is what the 60 ms buffer exists to bound.

- **A cost that does not move with the size of its input is a fold, not a
  measurement.** The check to run on every number above. Nine shapes of the
  row-skip micro-benchmark reported ~0 ns for a 40k-character `==`, on `String`,
  on a struct wrapping one, and on `Data` alike: the operands are pure expressions
  of a constant, so they get common-subexpression eliminated into one shared
  buffer and take the identity path the measurement was about. Hoisting the pool,
  `inout` + `@inline(never)`, a closure-built pool, 1k and 1M iterations, the
  operand hoisted and rebuilt per iteration — all fold. `let copy = t; t = copy`
  is a no-op and reads 0.000 ms at every size.

## Phase 3d: the views

The views were the only code no check reached, and the only way to see them was
to launch the app. Four gaps, and a way to look.

- **The answer is markdown.** `Markdown.blocks` (in `AgentAppCore`, 52 checks)
  turns an answer into headings, paragraphs, fenced code, both list kinds, a
  quote, a pipe table and a rule; `MarkdownView` draws them, with inline runs
  left to `AttributedString(markdown:)` on a block's text — never on the whole
  answer, because a fenced block's `**` would be eaten by an inline parser. A
  streaming answer is **not** parsed: O(n) per publish on a growing string is
  O(n²) per run, the thinking stream's trap again. The parser's rule is that no
  input line is ever dropped.
- **The tool row kept its own preview.** `Transcript.preview(of:)` folded
  newlines and cut at 240 characters, so expanding a row showed the arguments
  and never the output. The row keeps the result now — bounded at the **head**
  (8k, and it says so), where the thinking stream is bounded at its **tail**:
  a file starts at the top, and the part of the thinking that matters is the
  part that is arriving.
- **Scrolling followed the line count.** A 2k-character answer is one line, so
  its 1,300 deltas changed no count and the view sat still while the answer ran
  off the bottom; and it scrolled even when the user had scrolled up to read
  something. The signal is now the last line's *value*, acted on only while
  pinned, and `LazyVStack` gives the pin for free through the last line's
  `onAppear`/`onDisappear`. A "Latest" button appears when the user is away.
- **The reasoning control did not exist.** HANDOFF said "the toolbar exposes
  `reasoning_effort`"; no view set it, so the one control that turns the
  ten-seconds-of-nothing into 1.1 s was unreachable. It is in the toolbar now.
  The same bug was in the live check: `model.reasoningEffort = .none` is
  `Optional.none` — nil, "model decides" — so the run the check called the fast
  one was asking for the server default. The compiler warns and picks the wrong
  one; the case is spelled in full now.
- **The views are a library, and there is a preview.** `AgentAppUI` is a target;
  `AgentApp` is the `@main` that opens a window on it; `swift run ui-preview`
  feeds a scripted event stream through the real `AppModel` and opens the real
  `RootView`. No service, no model, no working-directory panel, and it can show
  a failed tool, a refused permission, a 40-line result and a table. Every
  screenshot in this phase came from it.
- **A toolbar group wider than the window overflows into a `>>` chevron**, and
  the window looks fine while the controls are gone. The model picker, the
  Thinking picker, the segmented permission control, the Session menu and the
  window title together exceeded 900 points; the working-directory control moved
  to the status bar, which already shows the path.

## What Phase 2 built

| File | What it is |
|---|---|
| `dev_docs/agent-service-protocol.md` | The contract: NDJSON JSON-RPC over a Unix socket, 16 methods, 19 events, error codes, ordering guarantees, the permission round-trip. The Swift app is written against this document. |
| `openagent/service/protocol.py` | The contract in code: envelope, framing (8 MiB cap), event codec, `EVENT_TYPES` registry, `RpcError` + `ErrorCode`. |
| `openagent/service/server.py` | `AgentService` + `Connection`. One run per connection, reader loop and run task separate, writes serialized, disconnect cancels the run, provider closed after every run, permission timeout denies. |
| `openagent/service/client.py` | `ServiceClient`: handshake, response/event demux, `start_run`, `respond_permission`, `events()`. |
| `openagent/service/cli.py` | `openagent serve` and `openagent tui` (headless client, interactive permission prompts). |
| `openagent/service/scope.py` | `working_dir` validation: absolute, existing, not `/`, inside `--root`. |
| `openagent/service/sessions.py` | Session ids (path-traversal-safe), save/load/list under `~/.openagent/sessions`. |
| `openagent/runtime/runner.py` | `AgentRunner`: the streaming, tool-capable run loop that **emits events instead of printing**. The CLI's `run_loop` prints to a terminal and cannot be a sidecar engine. |
| `openagent/runtime/permissions.py` | Risk classification + gates (`AllowAll`, `ReadOnly`, `Risk` with once/session/never scopes). Unknown tools classify as exec — fail closed. |
| `openagent/runtime/events.py` | Added `permission_requested`, `permission_resolved` (with `reason`), `AgentResult.stop_reason`. |
| `tests/support/streaming_provider.py` | `ScriptedStreamProvider` (turn scripts, tool calls, mid-stream failure, hang) and `HangingProvider`. |
| `tests/service/` | 81 contract tests over a **real socket**, plus fixtures for the Swift package. |
| `scripts/smoke_service.py` | End-to-end smoke over a real socket, sandboxed HOME. |
| `scripts/gen_protocol_fixtures.py` | Regenerates `tests/service/fixtures/{protocol,events}.json` from the Python types. |

## What Phase 2c built — the Swift client

| File | What it is |
|---|---|
| `macapp/AgentClient/Sources/AgentClient/Wire.swift` | Envelope, NDJSON `FrameDecoder` (8 MiB cap), `ErrorCode`, `JSONValue` (the only place that touches `Any`), the shared `convertFromSnakeCase` decoder. |
| `macapp/AgentClient/Sources/AgentClient/AgentEvent.swift` | Every event type 1:1 with the Python dataclasses, `.unknown` for events this client has never heard of. |
| `macapp/AgentClient/Sources/AgentClient/AgentServiceClient.swift` | Actor over `AF_UNIX`: handshake, request/response futures, event stream, `startRun`, `respondPermission`, model/tool/session/auth/config calls. |
| `macapp/AgentClient/Sources/AgentClient/ContractFixtures.swift` | Loads `tests/service/fixtures/*.json` — the fixtures the Python side generates. |
| `macapp/AgentClient/Sources/AgentClientChecks/` | `swift run agent-checks`: the contract suite that runs without Xcode. Spawns the real service and drives it. |
| `macapp/AgentClient/Tests/AgentClientTests/` | The same assertions as a Swift Testing suite, for when the package is opened in Xcode. |
| `macapp/AgentClient/Sources/AgentAppCore/Transcript.swift` | The event stream folded into the timeline a view renders: assistant lines, tool rows, permission prompts, compaction, run state. |
| `macapp/AgentClient/Sources/AgentAppCore/AppModel.swift` | The app's state: runs, permission answers, models, sessions, the session id taken from `run.start`. |
| `macapp/AgentClient/Sources/AgentAppCore/Sidecar.swift` | Spawns the service from the repo's venv in development; logs to a file, not a pipe. |
| `macapp/AgentClient/Sources/AgentApp/` | The SwiftUI shell: `solocoder`. |
| `macapp/README.md` | How to run it, what it covers, and the traps. |

### Gates passed

- `pytest tests -q` → 569 passed.
- `swift run agent-checks` → **165 checks passed**; with
  `AGENT_REPO_ROOT` + `AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s` → **180**: a real
  streamed run (tool calls, ~130 deltas, `run_completed`) folded through
  `Transcript` (8 lines, state "Done"), and the real `AppModel` + `Sidecar`
  finishing a run on a real model (7 lines, session id from `run.start`).
- `swift run solocoder` → the app opens, spawns the service, binds the socket,
  connects (verified by `lsof` on the socket pair; the window is the user's).
- `scripts/smoke_service.py` → `SMOKE OK` (handshake, run, permission round-trip,
  tool execution, streamed deltas, session persistence, reconnect).
- **Real model, real socket** (`lan/swift-1.5-iq3_s` @ 192.168.4.34:8080):
  - streamed run that called `glob`, read the result, answered;
  - `--permission-mode ask`: the TUI was prompted for `bash` and `write`,
    answered `y`, the file was really written, `read` ran unprompted (read-only);
  - a second run on the same `--session` remembered the file the first created
    (resume over a reconnecting client).
- `openagent serve --help` / `openagent tui --help` wired into `cli_coder.py`.

### Phase 1 gates still open (user action)

- `auth login anthropic_oauth` — needs an interactive browser.
- LM Studio API server on `:1234` — not enabled.
- `models use lan/swift-1.5-iq3_s` — left to the user. The router picks
  `lmstudio/qwen3-coder-32b` (unreachable) under `local_first`, so runs need an
  explicit `-m lan/swift-1.5-iq3_s` until a default is chosen.

## Commands

```bash
cd /Users/taozeng/Projects/SoloCoder
.venv/bin/python -m pytest tests -q                 # 576 passed
.venv/bin/python scripts/smoke_service.py           # SMOKE OK
.venv/bin/python cli_coder.py serve --socket /tmp/oa/e2e.sock
.venv/bin/python cli_coder.py tui --socket /tmp/oa/e2e.sock \
    -p "…" -w /tmp/oa/demo -m lan/swift-1.5-iq3_s --permission-mode ask
.venv/bin/python cli_coder.py models list --discover
.venv/bin/python scripts/gen_protocol_fixtures.py   # after any protocol/event change

cd macapp/AgentClient
swift run agent-checks                              # 382 checks passed
AGENT_REPO_ROOT=../.. swift run agent-checks        # 395, + the app-model checks
AGENT_REPO_ROOT=../.. AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s \
    swift run agent-checks                          # …and a real streamed run
AGENT_REPO_ROOT=../.. swift run solocoder           # the app (see the note below)
swift run ui-preview                                # the views, no service, no model
UI_PREVIEW_SHORT=1 swift run ui-preview             # …top of the timeline
swift run ui-cost                                   # streaming cost, measured

cd ../..
scripts/build_app.sh                                # the .app, 119 MB
scripts/build_app.sh --binary                       # …the fast path: binary + source
scripts/package_check.sh                            # 6 checks, 7 with AGENT_LIVE_MODEL
scripts/make_dmg.sh                                 # 62 MB dmg, verified from a mount
rm -rf /Applications/SoloCoder.app \
    && ditto .build/app/SoloCoder.app /Applications/SoloCoder.app   # refresh the install
```

## Next: Phase 3b — packaging the sidecar

*Done by Phase 3f, above: the answer was not PyInstaller, and the sandbox the
keychain item in point 4 assumes is not used.*

The app runs from a checkout (`Sidecar` spawns `.venv/bin/python cli_coder.py
serve`). A shipped app embeds a frozen build of the service, and that decision
drags in the rest:

1. **PyInstaller (or similar) build** of `cli_coder.py serve` — the entry point
   exists (`openagent serve`), so this is a spec file plus the hidden imports
   the providers need.
2. **Socket path inside the container**: `~/Library/Application Support/…` or an
   app group, kept short (AF_UNIX ~104 bytes).
3. **Launch/teardown**: the app owns the sidecar's life; SIGTERM cancels the run
   and closes the provider (already true), and `Sidecar.stop()` waits.
4. **Keychain access from a sandboxed app** — the model-server keys live in the
   login keychain; a sandboxed `.app` needs the entitlement, or the config moves.
5. **Code signing + notarization**, icon, `Info.plist`, `.app` bundle assembly
   (SwiftPM executables are not bundles).

## Next: the views, with a screen

*Done by phase 3h, above, except the last item.* Phase 3d did markdown rendering,
tool output beyond the preview, and scrolling; the restyle closed the list:

- the permission sheet renders the arguments as labelled sections — a `bash`
  prompt shows its command, a `write` its path and its content, not a JSON object
- the long session is measured (the `ui-cost` harness, sections 3–5) and is not
  the app's cost: 0.43 ms to copy and apply 4,001 lines, 0.46 ms to group them
  into turns, 0.12 ms for a 1,000-line turn's durations — 1.7% of the 60 ms
  cadence together
- the composer shows why `Send` is disabled, and `Return` no longer sends the
  prompt twice
- the header has a Copy answer button over `Transcript.finalText`

The last item is done, by phase 3i: the sidebar names sessions by a stored title
and deletes them, through `session.delete`.

## Things to know (verified the hard way)

- **A vertical `TextField` keeps its text when its binding is written from
  inside its own `onSubmit`.** The send lands — the prompt is in the timeline,
  the run starts — and the field still holds the prompt, so the next `Return`
  sends it a second time. Clear the draft where you read it *and* again on the
  next main-actor turn (`Task { @MainActor in draft = "" }`). Reproduced in
  `ui-preview` with a seeded draft; the fix is verified by the same test.
- **A `maxWidth`-only frame under a `nil` size proposal collapses a
  `NavigationSplitView`.** `.frame(maxWidth: 1240)` in a 1,240 pt window gave a
  590 pt split view, content left-aligned in the remainder: the proposal is nil
  for the root of a window, so only the ideal width is left. The frame needs
  `maxWidth: .infinity` (and `maxHeight: .infinity`) to say "fill, up to this".
- **An occluded window captures black.** `screencapture -l<id>` of a window
  fully behind another returns a black rectangle, and the app is fine: main
  thread idle in the run loop, `kCGWindowIsOnscreen` true, alpha 1, right
  bounds. Before believing a black screenshot, bring the window forward — the
  first black capture of the live app this session cost a `sample`, a
  `CGWindowListCopyWindowInfo` dump and a full-screen shot to find it out.
- **`swift run solocoder` from `macapp/AgentClient` fails `noService`.** The
  sidecar resolves the checkout from `AGENT_REPO_ROOT`, else the cwd if it has a
  `.venv`; from the Swift package there is none, so `resolveLaunch()` returns
  nil and the window opens with nothing to talk to. `AGENT_REPO_ROOT=../..` is
  the command from there — **not `..`**, which is the trap: the variable is
  resolved against the process cwd, the package is one directory deeper than
  `macapp`, so `..` names `macapp`, which has no `.venv`. The app says
  `noService`; the checks say `the root has an executable .venv/bin/python:
  …/macapp` and then `the app connected to the service it spawned (not
  connected)`. From the repository root itself, no variable is needed:
  `swift run --package-path macapp/AgentClient agent-checks`.

- **Screenshots are 2×, and `sips --cropOffset` is ignored.** A Retina capture of
  a 1,240 pt window is 2,480 px, so a crop measured in points lands a quarter of
  the way down the window and the reading is nonsense — rows looked duplicated,
  subtitles looked stale, for exactly that reason. For a region, capture it
  directly in points: `screencapture -x -R x,y,w,h`.
- **A sleeping display breaks capture and accessibility together.** The
  full-screen capture is black, `AXUIElementCopyElementAtPosition` answers
  `cannotComplete`, and an app launched while the display is asleep creates a
  window AppKit never exposes to the accessibility tree. `caffeinate -u` (and
  then relaunch the app) before believing any of it.
- **A killed app's window id keeps capturing.** The window server keeps a dead
  instance's window, and `screencapture -l<id>` returns a complete, plausible UI
  that produces nothing: no session file, no log line, no sidecar. Cross-check the
  owner pid from `CGWindowListCopyWindowInfo` against `ps` before driving it.
- **SwiftUI-hosted controls answer position queries, not child walks.** System
  Events' `entire contents` of the window returns the sidebar's rows and none of
  the composer's controls; `AXUIElementCopyElementAtPosition` finds them, with
  frames. So drive that band by scanning for the frame and clicking inside it —
  the footer's controls are 13 pt tall, and a click 3 pt above the frame is a
  click on nothing. (This is what made the sidebar footer look unreachable; it
  was 3 pt away.)
- **An `NSOpenPanel` opened from a SwiftUI button is not the key window.** Every
  keystroke goes to the main window, so the path typed into it vanishes.
  `perform action "AXRaise" of window "Open"` first; then the go-to-folder sheet
  (`Cmd-Shift-G`) takes its value set through accessibility, and `Return` is the
  panel's default button.
- **A per-delta cost that is O(n) in the text it appends to is a wedge, not a
  slowdown.** The answer's markdown re-parse fires on every newline and scans the
  whole answer: 57 ms per delta at 200k characters, one delta every 60 ms of
  stream. Bounds are the fix, and the probe has to contain the trigger — the
  harness's answer probe streamed one long line and measured 1.8 ms where the
  real shape measures 719 s. **Capping the string is only half of it**: the scan
  is O(n) in the capped string, once per newline, so 200k characters measured
  162 s with a 32k cap; gating *how often* it scans is the other half. The
  length test is not the cost — `String.utf8.count` is O(1) on a Swift String,
  ASCII or not, measured on 200k of each — so the search does not go there.
- **Bound the unit the screen pays, not only the unit the code pays.** With the
  fold measured at 386 ms for a 200k-character answer, the live looping run still
  pinned a core and grew RSS 6 MB/s, and `sample` named no frame from this
  codebase: the cost was SwiftUI laying out the streaming answer's 2,573 blocks —
  or one paragraph of 18,005 characters — every 60 ms. A character cap does not
  bound a *view* count, and one block is not one view: a paragraph is a text view
  of its whole length, and a bullet list is a row per item inside one block. So
  the drawing is bounded on both axes (60 blocks, 2,000 characters each) and the
  counts of what fell out are part of the core's answer, not invented by the view.
  The probe that found it prints `blocks.count` and the drawn characters, not the
  fold time (`ui-cost`, section 2c).
- **Probe services accumulate in `/tmp`.** A sidecar's socket directory is named
  after its parent's pid, so a probe that uses `/tmp/probehome` leaves a service
  with a name that says nothing. `pgrep -f "serve --socket"` before concluding
  that a socket is in use.

- **A `body` containing a `switch` must live in the struct, not an extension.**
  The result-builder transform is applied to a `body` in the type's own
  declaration only; in an extension the branches fail to unify ("branches have
  mismatching types", for branches that are all `View`s).
- **A published write is the unit of SwiftUI work, not a state change.** Folding
  a delta costs 6.5 µs; publishing it costs a view-graph re-render. Buffer the
  flood and publish on a cadence.
- **A selectable `Text` cannot be laid out incrementally.** Text selection is
  off while a line streams.
- **A killed app leaves its sidecar** unless the service watches stdin. Socket
  paths are pid-derived, so the orphan is invisible in the next launch's logs.

- **`ConfigManager.get_config_path()` respects `XDG_CONFIG_HOME`** — tests that
  touch config/auth must isolate `HOME` *and* `XDG_CONFIG_HOME`, and must not
  monkeypatch `ConfigManager._path`. `tests/conftest.py::isolated_config` does this.
- **`RpcError` must not be a frozen dataclass.** Python 3.14's `contextlib`
  assigns `__traceback__` on the way out of `asyncio.timeout`/`suppress`, and a
  frozen exception raises `FrozenInstanceError` *from inside the exception
  handler*. `tests/service/test_protocol.py::test_rpc_error_survives_contextlib_and_asyncio_timeout`
  pins it.
- **`Session.__len__` makes an empty session falsy.** `session or Session()`
  silently discards a caller's fresh session. `AgentRunner` compares to `None`;
  `tests/runtime/test_runner.py::test_runner_preserves_the_callers_session` pins it.
- **`Server.wait_closed()` waits for connection handlers.** A sidecar's clients
  stay connected for the life of the app, so `AgentService.stop()` cancels the
  connection tasks *before* awaiting `wait_closed()`, and background tasks
  (model discovery) are tracked separately from client tasks.
- **macOS `AF_UNIX` paths cap at ~104 bytes.** pytest's `tmp_path` is too long
  for a socket; `tests/service/test_server.py::Harness.sock_dir` uses a short
  `tempfile.mkdtemp(prefix="oa")` and skips if the platform refuses.
- **The service tests isolate `HOME`/`XDG_CONFIG_HOME`** (autouse fixture) —
  every run saves a session, and the suite must not litter `~/.openagent`.
- **`working_dir` is real.** `ToolContext.working_dir` is applied as the cwd of
  `bash` and resolved against `read`/`write`/`edit`/`glob`/`grep`/`apply_patch`.
  The model is given absolute paths; it cannot escape the session directory.
- **`run.start` answers before the first token**, and `permission_requested` is
  emitted *before* the engine blocks on the answer. Both are load-bearing for a
  UI; the contract tests assert them.
- **A denied tool emits no `tool_call_started`.** The refusal goes back to the
  model as an errored tool result, and `permission_resolved.reason` says why
  (user refused / timed out).
- **`--session NAME` creates as well as resumes.** Naming a session for its
  first run is the common case; `session.load` stays strict so a typo is not
  silently blank.
- **Model discovery is a startup task, not a blocking call.** A model on a
  user-added server is only in the catalog after `discover()`; `_resolve_model`
  discovers on a miss so `run.start(model=…)` never needs a prior `model.list`.
- **LM Studio is `:1234`.** `:1235` is a different server; `:8080` is
  llama.cpp/mlx; `:11434` ollama; `:8000` vllm.
- **The LAN model server** `lan` → `http://192.168.4.34:8080/v1`, key in the
  macOS Keychain, model `lan/swift-1.5-iq3_s` (tool-capable, 32k ctx).
- **`auth status` / `models list` are the state checks.** No API keys, no
  subscription sign-ins; `lan` is the only registered server.
- **`swift test` needs Xcode.** XCTest and Swift Testing ship inside
  `Xcode.app`; a Command Line Tools install has the compiler and not the test
  libraries, so `swift test` cannot build a suite here. `swift run agent-checks`
  is the gate that runs everywhere, and `Tests/AgentClientTests/` mirrors it for
  the day the package lands in Xcode.
- **`NSNumber` bridges to `Bool` for 1 and 0.** A JSON `1` answers `as? Bool`
  and becomes `true` — that turned `protocol_version` into a flag. Booleans are
  identified by identity against the `kCFBoolean` singletons before any numeric
  cast, in `JSONValue.fromJSON`, the only function allowed to see `Any`.
- **Every wire decoder uses `.convertFromSnakeCase`; `AgentEvent` does not.**
  The wire is snake_case by design, so result structs are camelCase. Event
  payloads are `JSONValue` dictionaries whose keys are snake_case literals — the
  strategy would rewrite them.
- **Raw values are wire literals.** `PermissionMode.allowReadonly` must be
  `"allow_readonly"`. `JSONEncoder` emits the raw value, and the server answers
  `permission_mode must be one of allow_all, allow_readonly, ask`. The live
  check caught this; a unit test against the fixtures would not have.
- **`config.get` takes `keys` (a list) and answers `{"config": {…}}`**, narrowed
  to the sections asked for; `config.set` takes `values` and only accepts
  `policy`, `default_model`, `roles`, `provider`. Credentials are not settable
  over the socket — they go through the CLI, where a human types them.
- **Session saves are atomic.** `sessions.save` serializes to a sibling temp file
  and `os.replace`s it. A plain `write_text` truncates first, so a SIGTERM
  mid-write replaces a conversation with an empty file — one was found on disk,
  written by a suite that kills its own server. Pinned by
  `tests/service/test_sessions.py::test_a_save_killed_midwrite_leaves_the_previous_session_intact`.
- **`AGENT_LIVE_MODEL` opts out of the sandboxed `HOME`.** A real run routes to
  the developer's model servers, whose config and Keychain keys live in the real
  `HOME`; a sandboxed one has nothing to route to, and the run writes a session
  into `~/.openagent/sessions`.
- **The event stream is the connection's, not the run's.** The Swift client used
  to `finish()` its stream at the first terminal event, which left the app with
  no events for its second run on the same connection — every run after the
  first. The stream now ends when the connection does; one-shot consumers break
  on `isTerminal`, and `Transcript` closes each run. Found by the app-model
  check, invisible to the protocol tests.
- **`AppModel` and `Sidecar` live in `AgentAppCore`, not in the executable
  target.** A target a test cannot import is a target a test cannot check. The
  views are the only part left unchecked, which is why they are thin.
- **`Sidecar` redirects the service's stderr to a file, not a `Pipe`.** A full
  pipe buffer blocks the writer: a service that blocks on its own stderr is a
  service that stops serving.
- **A SwiftPM executable's types cannot be imported.** That is why `AgentAppCore`
  is a library, and why the views moved to `AgentAppUI`: with them in the
  executable, nothing — not a check, not a preview — could put them on screen.
- **`.none` on an optional enum property is `nil`, not the enum's `.none` case.**
  `model.reasoningEffort = .none` compiles, warns, and means "model decides".
- **`hello` reports `terminal_event_types`** and a client ends a *run* on the
  union of its own set and the server's, so a terminal event added in a later
  protocol version closes an older client's timeline instead of leaving it open.

- **A store that reads one backend first needs a documented way to discard what
  it found there.** Keychain-then-file means a half-written keychain value is
  returned forever, and the intact file copy is unreachable. The read path needs
  a "this one is unusable, give me the next" — and the write path needs to delete
  what it failed to write.
- **A test that asserts the code's spelling of a vendor field proves nothing.**
  `plan_type` vs `chatgpt_plan_type`: the fixture and the code agreed, the suite
  was green, and the field was blank for every real sign-in. Fixtures for
  third-party payloads should be copied from a real payload.

## Git

Ahead of `origin/feat/computer-use-macos` by the phase 0–3 commits (nothing
pushed), newest first:
`phase3a: Transcript — the event stream as UI state, checked headless`,
`phase2c: Swift AgentClient — protocol codec, framing, live contract checks`,
`phase2b: Unix-socket agent service, client, CLI, contract tests`,
`phase2a: wire protocol + streaming tool loop (AgentRunner)`,
`phase1+: working_dir was decorative — tools wrote into the process CWD`,
`phase1+: user-added model servers, discovery diagnostics, test isolation`,
`phase1: provider registry, model catalog, router, auth broker`,
`phase0: foundation cleanup for the Mac general-agent app`.
