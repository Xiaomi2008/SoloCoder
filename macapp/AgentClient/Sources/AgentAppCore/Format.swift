import Foundation

/// The clock's answer, in the words a row shows.
///
/// A timeline says how long something took in the units it takes: `12s`, `2m 50s`,
/// `1h 4m`. Two units at most, and the smaller unit is dropped below a minute —
/// `2m 0s` reads as a clock that is broken, and `120s` is a number the reader has
/// to divide.
///
/// `nil` and a negative interval answer `nil`, not "0s": a session loaded from
/// disk has no arrival times, and a row that claims it worked for zero seconds is
/// a row that lies. The view shows the shorter label ("Worked") when this returns
/// nil, which is the same answer with the number left out.
public enum DurationFormat {
    public static func short(_ seconds: TimeInterval?) -> String? {
        guard let seconds, seconds >= 0.5 else { return nil }
        let whole = Int(seconds.rounded())
        if whole < 60 { return "\(whole)s" }
        let minutes = whole / 60
        let rest = whole % 60
        if minutes < 60 { return rest == 0 ? "\(minutes)m" : "\(minutes)m \(rest)s" }
        let hours = minutes / 60
        return "\(hours)h \(minutes % 60)m"
    }

    /// The interval between two arrivals, and nothing when the clock was not
    /// running for them (a history loaded from disk, where every line arrives at
    /// once).
    public static func between(_ start: Date?, _ end: Date?) -> TimeInterval? {
        guard let start, let end else { return nil }
        let seconds = end.timeIntervalSince(start)
        return seconds >= 0 ? seconds : nil
    }
}
