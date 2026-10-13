import AgentAppCore
import AgentClient
import Foundation

// A realistic run: 3 turns, 180 assistant messages, ~9000 deltas (the model
// streams ~1.5 chars per chunk), 180 tool rows. What does folding it cost, and
// how much work does one delta cost as the timeline grows?
//
// What it says, on a release build. A debug build reads 4.6× higher on the fold
// (23 µs per event), 14× on a delta (87 µs) and 13× on a publish (92 µs at 401
// lines) — it does not inline, so the constant cost per call dominates and the
// *growth* terms shrink: the answer fold's last-1000 cost goes 0.6 → 1.9 µs in
// debug against 0.1 → 1.4 µs in release. Read the release numbers; the debug ones
// are the same reason the take-out-and-put-back idiom is slower in debug (HANDOFF).
//
//   fold            5 µs per event; a 401-line session is 44 ms, once, off the main thread
//   one delta       6 µs, flat in timeline length — the delta is applied in place
//   thinking stream flat: 0.2 ms per 1000 deltas at the end of a 480k-character stream,
//                   which is the cap (`Transcript.reasoningKeep`) holding
//   the answer      flat, and bounded: 0.1 → 0.9 µs per delta streaming one long
//                   line, and 13 µs per delta with a newline in every one at 200k
//                   characters — `answerTrimAt` bounds the string, `answerReparseEvery`
//                   bounds the scans. Ungated and uncapped that last case was 57 ms
//                   per delta and 719 s for the run
//   what a publish draws  the number that wedged the app, and it is not a fold
//                   number (section 2c). A looping answer leaves 2,573 blocks with
//                   blank lines, one paragraph of 18,005 characters without them,
//                   or one bullet list of 11,245 — and a publish lays all of it out,
//                   every 60 ms, for as long as the run does not stop. The fold of
//                   the same 24k is 105 ms for the whole run, so the fold is not
//                   where the core went. `streamingBlocks` bounds the drawing to 60
//                   blocks of 2,000 characters and counts what the bound left out
//   one publish     7 µs at 401 lines, 66 µs at 4001: the copy-on-write copy of the
//                   lines array, paid on the next mutation, linear in line count,
//                   ~0.1% of the 60 ms cadence
//   the row skip    O(1) per row per publish, because an unchanged row's string
//                   shares its buffer and `==` starts with an identity check
//   the turns       0.45 ms to group 4,001 lines, and 0.12 ms for the durations
//                   of a 1,000-line turn — 2% of the 60 ms cadence together
//
// So the transcript is not what a long session costs the app. The views are: a
// publish is a view-graph re-render, and the 60 ms buffer is what keeps the flood
// of deltas from being one re-render each (see the HANDOFF note on published
// writes). That last sentence is not a hypothesis any more — the live looping run
// pinned a core with the fold at 386 ms, and `sample` named SwiftUI's layout and
// nothing from here. So the fold numbers are the floor, and section 2c's drawn
// counts are the thing to watch.

func ev(_ s: String) -> AgentEvent { try! AgentEvent.decode(from: Data(s.utf8)) }

func fold(turns: Int, lines: Int, chunks: Int) -> (Transcript, Double) {
    var transcript = Transcript()
    transcript.addUserMessage("do the thing")
    let t0 = DispatchTime.now().uptimeNanoseconds
    for turn in 0..<turns {
        // A new run, not a new turn of the same one. `Transcript` refuses a
        // non-terminal event after a terminal one — that is the rule that keeps a
        // late arrival from resurrecting a finished run — so a harness that ends
        // each turn with `run_completed` and then keeps applying events is
        // measuring one turn, not the session it drew. The app opens each run
        // with `beginRun`, and so does this.
        transcript.beginRun(runId: "r\(turn)")
        transcript.apply(ev(#"{"type":"run_started","run_id":"r\#(turn)"}"#))
        for line in 0..<lines {
            let id = "m\(turn)-\(line)"
            transcript.apply(ev(#"{"type":"message_started","run_id":"r","message_id":"\#(id)"}"#))
            for chunk in 0..<chunks {
                transcript.apply(
                    ev(#"{"type":"message_delta","run_id":"r","message_id":"\#(id)","delta":"tok\#(chunk) "}"#)
                )
            }
            transcript.apply(
                ev(#"{"type":"message_completed","run_id":"r","message_id":"\#(id)","output_text":"a reply long enough to be laid out, with words and punctuation and a sentence"}"#)
            )
            transcript.apply(
                ev(#"{"type":"tool_call_started","run_id":"r","message_id":"\#(id)","tool_call_id":"t\#(turn)-\#(line)","tool_name":"bash","arguments":{"command":"ls -la /some/where"}}"#)
            )
            transcript.apply(
                ev(#"{"type":"tool_call_completed","run_id":"r","message_id":"\#(id)","tool_call_id":"t\#(turn)-\#(line)","tool_name":"bash","result":"file a\nfile b\nfile c\n"}"#)
            )
        }
        transcript.apply(
            ev(#"{"type":"run_completed","run_id":"r","final_message_id":"m","result":{"run_id":"r","final_message_id":"m","output_text":"done","stop_reason":"end_turn"}}"#)
        )
    }
    return (transcript, Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e9)
}

let (transcript, seconds) = fold(turns: 3, lines: 60, chunks: 50)
let events = 3 * (1 + 60 * (1 + 50 + 1 + 2) + 1)
print("lines: \(transcript.lines.count), events: \(events)")
print(String(format: "fold cost: %.1f ms  (%.2f us per event)", seconds * 1000, seconds / Double(events) * 1e6))

// The cost of one delta at the end of a long timeline. It is flat in timeline
// length: the delta is applied to the transcript the model owns, in place, so the
// lines array is not shared and nothing is copied. The copy is the publish's, and
// section 3 measures it.
var probe = transcript
// The fold ends on a terminal event, and a finished transcript refuses a
// non-terminal one. A probe that ignores that measures a `return`, not a delta.
probe.beginRun(runId: "probe")
let t1 = DispatchTime.now().uptimeNanoseconds
for _ in 0..<1000 {
    probe.apply(ev(#"{"type":"message_delta","run_id":"r","message_id":"m0-0","delta":"x"}"#))
}
let ms = Double(DispatchTime.now().uptimeNanoseconds - t1) / 1e6
print(String(format: "one delta on a %d-line timeline: %.3f ms", transcript.lines.count, ms / 1000))

// MARK: - The thinking stream

// A reasoning model's private tokens arrive *before* the answer, and there are
// far more of them than of the answer: measured on `lan/swift-1.5-iq3_s`,
// ~1,900 reasoning tokens before the first answer character, and one run
// produced 686,439 characters of reasoning with no answer at all. So the answer
// path is not the interesting one — the thinking path is where a run is long,
// and it is the path the 60 ms buffer never covered.
//
// The number that matters is the per-delta cost as the accumulated text grows.
// A string read out of an array element is shared with it, so appending to a
// line's text copies the whole accumulated thinking: O(n) per delta, O(n²) per
// run, and every publish copies and compares the same string. The line therefore
// keeps a tail, not the whole stream — `Transcript.reasoningKeep`. A flat
// head/tail pair is that cap holding; the growth this measured before it was
// 0.4 ms for the first 1,000 deltas and 5.8 ms for the last 1,000 of a 480k
// character stream, 267 ms of memcpy for the run.
func foldReasoning(chars: Int, chunk: Int) {
    var t = Transcript()
    t.addUserMessage("do the thing")
    t.apply(ev(#"{"type":"run_started","run_id":"r"}"#))
    t.apply(ev(#"{"type":"message_started","run_id":"r","message_id":"m"}"#))
    let payload = String(repeating: "x", count: chunk)
    let event = ev(#"{"type":"reasoning_delta","run_id":"r","message_id":"m","delta":"\#(payload)"}"#)
    let n = chars / chunk
    var head = 0.0, tail = 0.0
    let t0 = DispatchTime.now().uptimeNanoseconds
    for i in 0..<n {
        let a = DispatchTime.now().uptimeNanoseconds
        t.apply(event)
        let dt = Double(DispatchTime.now().uptimeNanoseconds - a) / 1e3 // µs
        if i < 1000 { head += dt } else if i >= n - 1000 { tail += dt }
    }
    let total = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
    print(String(
        format: "reasoning fold: %d deltas, %d chars: %.1f ms total; first 1000 %.1f ms, last 1000 %.1f ms",
        n, chars, total, head / 1e3, tail / 1e3
    ))
}

foldReasoning(chars: 12_000, chunk: 6)
foldReasoning(chars: 120_000, chunk: 6)
foldReasoning(chars: 480_000, chunk: 6)

// MARK: - A long session
//
// A session is not one run. The app keeps the timeline, a session browser opens
// an old one into it, and a long answer is not rare: a coding agent writes a
// 40k-character explanation with code blocks in it. So the questions are what a
// *long* timeline costs, in the four places a value-type transcript pays:
//
//   1. folding a 200-line session, and one delta at the end of it;
//   2. appending to a long answer — the copy-on-write trap the thinking stream
//      was bounded to escape, on the path that is deliberately uncapped;
//   3. one published write, which copies the transcript value;
//   4. the `.equatable()` row skip, which compares a line to its previous value
//      for every visible row on every publish.
//
// The build matters: a debug build does not inline, and the string-copy idiom
// behaves differently in it (see the HANDOFF note on take-out-and-put-back).

#if DEBUG
print("build: debug")
#else
print("build: release")
#endif

// 1. A 200-line session.

let (long, longSeconds) = fold(turns: 4, lines: 50, chunks: 40)
let longEvents = 4 * (1 + 50 * (1 + 40 + 1 + 2) + 1)
print("session lines: \(long.lines.count), events: \(longEvents)")
print(String(format: "  fold: %.1f ms (%.2f us per event)", longSeconds * 1000, longSeconds / Double(longEvents) * 1e6))

var longProbe = long
longProbe.beginRun(runId: "probe")
let lt0 = DispatchTime.now().uptimeNanoseconds
for _ in 0..<1000 {
    longProbe.apply(ev(#"{"type":"message_delta","run_id":"r","message_id":"m0-0","delta":"x"}"#))
}
print(String(
    format: "  one delta on a %d-line timeline: %.3f ms",
    long.lines.count, Double(DispatchTime.now().uptimeNanoseconds - lt0) / 1e6 / 1000
))

// 2. Appending to a long answer.
//
// The answer is uncapped on purpose — it is the product, shown in full and
// copied out — so this is the O(n²) the thinking stream was bounded away from,
// left in on purpose. The question is how expensive "on purpose" is.
func foldAnswer(chars: Int, chunk: Int) {
    var t = Transcript()
    t.addUserMessage("do the thing")
    t.apply(ev(#"{"type":"run_started","run_id":"r"}"#))
    t.apply(ev(#"{"type":"message_started","run_id":"r","message_id":"m"}"#))
    let payload = String(repeating: "x", count: chunk)
    let event = ev(#"{"type":"message_delta","run_id":"r","message_id":"m","delta":"\#(payload)"}"#)
    let n = chars / chunk
    var head = 0.0, tail = 0.0
    let t0 = DispatchTime.now().uptimeNanoseconds
    for i in 0..<n {
        let a = DispatchTime.now().uptimeNanoseconds
        t.apply(event)
        let dt = Double(DispatchTime.now().uptimeNanoseconds - a) / 1e3
        if i < 1000 { head += dt } else if i >= n - 1000 { tail += dt }
    }
    let total = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
    print(String(
        format: "answer fold: %6d deltas, %7d chars: %8.1f ms total; first 1000 %.1f ms, last 1000 %.1f ms",
        n, chars, total, head / 1e3, tail / 1e3
    ))
}

foldAnswer(chars: 2_000, chunk: 6)
foldAnswer(chars: 20_000, chunk: 6)
foldAnswer(chars: 80_000, chunk: 6)

// 2b. The same answer, with newlines in it.
//
// The probe above streams one long line, and `appendAnswerDelta` re-parses the
// finished markdown prefix only when a newline lands — so it measures the append
// and never reaches the parse. A real answer has newlines, and a model that
// loops streams blank lines at it without stopping: the parse is O(n) in the
// whole answer, once per newline, so the answer's cost is quadratic in the
// answer, not linear.
//
// This is the shape that wedged the app: a run on a resumed session, main thread
// pinned at 100% in `Markdown.blocks`, RSS climbing 19 MB/s, and the spin
// continuing after the run was cancelled — the text was already there, and the
// view lays it out on every publish.
func foldAnswerParsed(chars: Int, chunk: Int) {
    var t = Transcript()
    t.addUserMessage("do the thing")
    t.apply(ev(#"{"type":"run_started","run_id":"r"}"#))
    t.apply(ev(#"{"type":"message_started","run_id":"r","message_id":"m"}"#))
    // A JSON escape, not a raw newline: this goes into a JSON string literal.
    let payload = String(repeating: "x", count: chunk - 2) + "\\n\\n"
    let event = ev(#"{"type":"message_delta","run_id":"r","message_id":"m","delta":"\#(payload)"}"#)
    let n = chars / chunk
    var head = 0.0, tail = 0.0
    let t0 = DispatchTime.now().uptimeNanoseconds
    for i in 0..<n {
        let a = DispatchTime.now().uptimeNanoseconds
        t.apply(event)
        let dt = Double(DispatchTime.now().uptimeNanoseconds - a) / 1e3
        if i < 1000 { head += dt } else if i >= n - 1000 { tail += dt }
    }
    let total = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
    print(String(
        format: "answer fold, newline per delta: %6d deltas, %7d chars: %8.1f ms total; first 1000 %.1f ms, last 1000 %.1f ms",
        n, chars, total, head / 1e3, tail / 1e3
    ))
}

foldAnswerParsed(chars: 20_000, chunk: 8)
foldAnswerParsed(chars: 80_000, chunk: 8)
foldAnswerParsed(chars: 200_000, chunk: 8)

// 2c. What the looping answer leaves for the view.
//
// The cap bounds the answer's characters and the gate bounds the scans, and the
// app *still* wedged on a live looping run: main thread pinned at 100%, RSS
// climbing 6 MB/s (178 MB → 782 MB in two minutes), and `sample` putting the
// main thread inside SwiftUI's attribute graph — `AG::Subgraph::update`,
// `ViewLayoutEngine.sizeThatFits`, `SelectionOverlay.updateNSView` — with **no
// frame from this codebase in it at all**. The reducer is not the cost.
//
// The reason is the unit. 32,000 characters of *list* is not one long string the
// view draws once; it is a row per line, and the streaming answer draws its
// finished blocks as a view per block. So the number that predicts the wedge is
// not the character count, it is `blocks.count` — the count of views the 60 ms
// publish lays out, every time.
func loopAnswer(chars: Int, line: String, blank: Bool, label: String = "") {
    var t = Transcript()
    t.addUserMessage("do the thing")
    t.apply(ev(#"{"type":"run_started","run_id":"r"}"#))
    t.apply(ev(#"{"type":"message_started","run_id":"r","message_id":"m"}"#))
    let payload = line + "\\n" + (blank ? "\\n" : "")
    let event = ev(#"{"type":"message_delta","run_id":"r","message_id":"m","delta":"\#(payload)"}"#)
    let n = chars / (line.utf8.count + 2 + (blank ? 2 : 0))
    let t0 = DispatchTime.now().uptimeNanoseconds
    for _ in 0..<n { t.apply(event) }
    let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
    guard let out = t.lines.last(where: { if case .assistant = $0.kind { return true } else { return false } })
    else { return }
    var kept = 0
    if case .assistant(let text) = out.kind { kept = text.utf8.count }
    // The characters the view lays out as text views: one block is one view, and
    // one paragraph block is one text view however long it is.
    let shown = out.blocks.reduce(0) { $0 + blockChars($1) }
    // What the bound leaves the view: the blocks it draws, and the characters in
    // them. This is the row that matters — the fold was never the cost.
    let drawn = out.streamingBlocks
    print(String(
        format: """
            loop answer%@: %7d chars streamed: fold %6.1f ms; kept %6d chars, %5d blocks, \
            %6d chars in them, tail %5d chars
              drawn: %3d blocks of %5d chars (was %5d of %6d); %d blocks and %d chars left out
            """,
        label, chars, ms, kept, out.blocks.count, shown, out.tailText.utf8.count,
        drawn.shown.count, drawn.shown.reduce(0) { $0 + blockChars($1) }, out.blocks.count, shown,
        drawn.hiddenBlocks, drawn.cutChars
    ))
}

/// The characters of a block, the way the view sees them: one text view per block.
func blockChars(_ block: MarkdownBlock) -> Int {
    switch block {
    case .paragraph(let text): return text.utf8.count
    case .heading(_, let text): return text.utf8.count
    case .code(_, let text): return text.utf8.count
    case .bullets(let items): return items.reduce(0) { $0 + $1.utf8.count }
    case .numbered(let items): return items.reduce(0) { $0 + $1.text.utf8.count }
    case .quote(let lines): return lines.reduce(0) { $0 + $1.utf8.count }
    case .table(let rows): return rows.reduce(0) { r, row in r + row.reduce(0) { $0 + $1.utf8.count } }
    case .rule: return 3
    }
}

loopAnswer(chars: 8_000, line: "apple", blank: false)
loopAnswer(chars: 24_000, line: "apple", blank: false)
loopAnswer(chars: 64_000, line: "apple", blank: false)
loopAnswer(chars: 8_000, line: "apple", blank: true)
loopAnswer(chars: 24_000, line: "apple", blank: true)
loopAnswer(chars: 64_000, line: "apple", blank: true)
// The third shape, and the one a "list every file" answer takes: a bullet per
// line, no blank lines, so one `.bullets` block of thousands of items — one block
// to the counter above, thousands of rows to the view.
loopAnswer(chars: 24_000, line: "- apple", blank: false, label: " (bullets)")
loopAnswer(chars: 64_000, line: "- apple", blank: false, label: " (bullets)")

// 3. One published write.
//
// `AppModel.flushDeltas` assigns the transcript once per publish, and the
// transcript is a struct holding an array of lines. The assignment itself is a
// refcount bump — copy-on-write, so nothing is copied yet. The copy is paid on
// the *next* mutation: the array is shared, so `apply` has to take a private copy
// of all 401 lines before it can write one. That is the per-publish cost a long
// timeline adds to the 60 ms cadence, and it is the take-out-and-put-back idiom
// the HANDOFF notes on.
//
// Measured at two sizes, because a cost that does not move with the size of its
// input is measuring nothing (see section 5). The first version of this loop was
// `let copy = t; t = copy`, which is a no-op the optimizer drops: it read 0.000 ms
// at every size.
func publishCost(_ base: Transcript, times: Int) -> (lines: Int, ms: Double) {
    var published = base
    published.beginRun(runId: "probe")
    let delta = ev(
        #"{"type":"message_delta","run_id":"r","message_id":"m0-0","delta":"x"}"#
    )
    let t0 = DispatchTime.now().uptimeNanoseconds
    for _ in 0..<times {
        var next = published // shares the lines array
        next.apply(delta) // shared, so this copies the array to write one line
        published = next
    }
    return (published.lines.count, Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6 / Double(times))
}
for (turns, lines, chunks) in [(4, 50, 40), (40, 50, 40)] {
    let (base, _) = fold(turns: turns, lines: lines, chunks: chunks)
    let (count, ms) = publishCost(base, times: 1000)
    print(String(format: "one publish (copy + apply) on %d lines: %.3f ms", count, ms))
}

// 4. The turn grouping.
//
// The timeline is grouped into turns for the screen (a prompt, the work, the
// answers), and the grouping is computed, not stored: `transcript.turns` walks
// the lines, and the view calls it once per publish. Two costs follow, and the
// second is the one worth measuring:
//
//   a. `turns` itself — O(lines) per publish;
//   b. the durations the reasoning row answers "Reasoned for 12s" from.
//
// b. is the one that was measured first, and it was quadratic. The first version
// was `Turn.nextArrival(after:)`, a scan from a line to the next one: O(lines)
// per reasoning row, so 10.532 ms over the 500 activity rows of a 1,000-line
// turn, paid again on every publish while that disclosure is open — a sixth of
// the 60 ms cadence, growing with the square. `Turn.durations` answers the same
// question from one pass over the lines, keyed by line id, and the view reads it
// once per turn render: 0.1231 ms on the same shape, 85× cheaper and linear.
//
// The lesson is the shape, not the number. A per-row accessor that walks the
// turn is fine at 20 rows and is the cost of the app at 1,000, and the view has
// no way to see it — the row asks for one value and gets a loop.
func turnCost(_ base: Transcript, times: Int) -> (turns: Int, lines: Int, ms: Double) {
    let t0 = DispatchTime.now().uptimeNanoseconds
    var sink = 0
    for _ in 0..<times { sink += base.turns.count }
    let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6 / Double(times)
    precondition(sink > 0)
    return (sink / times, base.lines.count, ms)
}
for (turns, lines, chunks) in [(4, 50, 40), (40, 50, 40)] {
    let (base, _) = fold(turns: turns, lines: lines, chunks: chunks)
    let (t, l, ms) = turnCost(base, times: 1000)
    print(String(format: "turns: %d turns over %d lines: %.4f ms per call", t, l, ms))
}

// b. The reasoning row's scan, over the worst turn the shape allows.
let (wide, _) = fold(turns: 1, lines: 500, chunks: 1)
if let turn = wide.turns.first {
    let t0 = DispatchTime.now().uptimeNanoseconds
    var found = 0
    for _ in 0..<100 { found += turn.durations.count }
    let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6 / 100
    precondition(found > 0)
    print(String(
        format: "durations of a %d-line turn: %.4f ms per render",
        turn.lines.count, ms
    ))
}

// 5. The row skip.
//
// `.equatable()` on a row asks "did this line change?" for every visible row on
// every publish. The question is what that costs for a row holding a 40k-character
// answer, and the answer turns on a detail of `String`: `==` starts with an
// identity check, so two strings that share a buffer compare in O(1) whatever
// their length.
//
// Which case the app is in follows from how the transcript is updated. A publish
// *copies* the transcript value; it does not rebuild the lines. Copy-on-write
// leaves every untouched line's string sharing its buffer with the copy, so an
// unchanged row — every row but the streaming one — is the identity case, and the
// row skip is O(1) per row per publish, independent of how long the answers are.
// The streaming row's text is a fresh buffer with a different length, so it fails
// the identity check and then the length check, and pays neither a compare nor a
// skip.
//
// The case that is not the app's is a line whose text was rebuilt from bytes — a
// session loaded from disk, a line re-rendered through a decode. Its cost is a
// memcmp of the whole text: hundreds of nanoseconds at 40k characters, which is
// why the identity path is the fact that matters.
//
// This is the one number the harness does not measure, and it is worth recording
// why. Every shape tried reported ~0 ns for a 40k-character compare, on
// `TranscriptLine` and on `Data` alike, because the operands are pure expressions
// of a constant and get common-subexpression eliminated into one shared buffer —
// which then takes the identity path the measurement is about. Shapes tried: a
// loop-invariant `a == b` (hoisted out, loop dead); the pool built by the caller
// and passed `inout` to an `@inline(never)` function (elements constant-propagated
// from the caller); the pool built by a closure argument (inlined, pool became a
// constant); the pool and operand built open-coded at top level, at 1,000 and at
// 1,000,000 iterations, with the operand hoisted and rebuilt per iteration, on
// `String`, on a struct wrapping one, on a struct wrapping an enum of one, and on
// `Data`. The last of those reads 9 ns flat across 2k, 20k and 40k characters —
// flat in length is the signature of the identity path, not of a byte compare.
// A measurement that is flat in the size of its input is measuring nothing.
//
// The lesson generalizes to the rest of this file, and is the check to run on any
// number here: if a cost does not move with the size of the thing being measured,
// the optimizer folded the work out. The measurements above all move — the answer
// fold's last 1000 deltas go from 0.1 ms to 1.3 ms as the answer grows to 80k
// characters, because `Transcript.apply` is a real call through a module boundary
// that cannot be inlined away.
