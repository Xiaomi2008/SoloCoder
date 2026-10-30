import Foundation

/// A request from the app shell or the Edit menu to put the clipboard's image
/// into the composer. The view owns the attachments; the event monitor and the
/// menu command only know the pasteboard.
public extension Notification.Name {
    static let solocoderPasteImage = Notification.Name("solocoder.pasteImage")
}

/// A searchable line in the command palette.
///
/// The palette's rows are drawn by the view, but *what a query selects and in
/// what order* is a rule, so it lives here where `agent-checks` can reach it.
/// A command palette that silently ranks an exact match below a substring is a
/// palette that makes the user type more than they need to.
public struct CommandLabel: Equatable, Sendable {
    public let title: String
    public let detail: String

    public init(title: String, detail: String) {
        self.title = title
        self.detail = detail
    }
}

/// How the palette answers a typed query.
public enum CommandSearch {
    /// The indices of the labels that match, best first.
    ///
    /// A query is a set of terms and every term must appear — in the title or
    /// in the detail. `⌘K` then `thinking off` selects the one row that names
    /// both, not every row whose title happens to contain "thinking".
    ///
    /// Ranking, highest first:
    /// - the title *is* the query: 100
    /// - a term starts the title: 40
    /// - a term is elsewhere in the title: 20
    /// - a term is in the detail: 8
    ///
    /// Ties keep the list's own order, which is the order the app's actions are
    /// written in: the common ones are first, and an empty query shows that
    /// order unchanged.
    public static func rank(_ labels: [CommandLabel], query: String) -> [Int] {
        let terms = terms(query)
        guard !terms.isEmpty else { return Array(labels.indices) }

        var scored: [(index: Int, score: Int)] = []
        for (index, label) in labels.enumerated() {
            let title = label.title.lowercased()
            let detail = label.detail.lowercased()
            var score = 0
            var matchesEveryTerm = true

            for term in terms {
                let inTitle = title.contains(term)
                let inDetail = detail.contains(term)
                if !inTitle && !inDetail {
                    matchesEveryTerm = false
                    break
                }
                if title == term {
                    score += 100
                } else if inTitle {
                    score += title.hasPrefix(term) ? 40 : 20
                }
                if inDetail { score += 8 }
            }

            if matchesEveryTerm {
                scored.append((index, score))
            }
        }

        return scored
            .sorted { $0.score != $1.score ? $0.score > $1.score : $0.index < $1.index }
            .map(\.index)
    }

    /// The query as lowercase terms, with surrounding whitespace ignored.
    ///
    /// Splitting on whitespace and not on punctuation means `⌘K` then `swift
    /// run` is two terms, while `lan/swift` stays one — a model key is a single
    /// thing the user types, and splitting it would match half the table.
    public static func terms(_ query: String) -> [String] {
        query.lowercased()
            .split(whereSeparator: { $0.isWhitespace })
            .map(String.init)
    }
}
