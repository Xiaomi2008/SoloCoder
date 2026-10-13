import AgentAppCore
import AgentClient
import Foundation

// A realistic run: 3 turns, 180 assistant messages, ~9000 deltas (the model
// streams ~1.5 chars per chunk), 180 tool rows. What does folding it cost, and
// how much work does one delta cost as the timeline grows?

func ev(_ s: String) -> AgentEvent { try! AgentEvent.decode(from: Data(s.utf8)) }

func fold(turns: Int, lines: Int, chunks: Int) -> (Transcript, Double) {
    var transcript = Transcript()
    transcript.addUserMessage("do the thing")
    let t0 = DispatchTime.now().uptimeNanoseconds
    for turn in 0..<turns {
        transcript.apply(ev(#"{"type":"run_started","run_id":"r"}"#))
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

// The cost of one delta at the end of a long timeline. This is what a view
// invalidation carries, because the transcript is a value type and every delta
// copies it — and every delta is one SwiftUI invalidation.
var probe = transcript
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
