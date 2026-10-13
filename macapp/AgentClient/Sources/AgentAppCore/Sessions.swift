import AgentClient
import Foundation

/// What a stored session is called, and what it answers to.
///
/// The service bounds a title at 200 characters — a payload bound, so
/// `session.list` fits one frame — and says nothing about how a name looks on a
/// row 232 points wide. That decision is here, where `agent-checks` can reach
/// it, and the view adds the one thing a character count cannot know: the
/// ellipsis at the width the row actually has.
public enum SessionTitle {
    /// The row's first line.
    ///
    /// A damaged file is named for what it is: its id is a 28-character string
    /// the user cannot read as a name, and the row already carries a warning
    /// glyph — the label has to say what the glyph means, because the row is
    /// also the way to delete the file.
    ///
    /// A session with no title — one that opens with a screenshot, a file from
    /// before titles — falls back to its id, which is at least the string
    /// `--session` takes.
    public static func label(_ row: SessionRow) -> String {
        if row.error != nil { return "Damaged session" }
        guard let title = row.title, let line = firstLine(title) else { return row.sessionId }
        return line
    }

    /// The searchable text of a row: the name, the id and the folder.
    ///
    /// The id stays in the match because it is what the CLI takes: a user who
    /// ran `--session fix-flaky` looks for that string in the sidebar, and the
    /// row shows a sentence, not the id.
    public static func matches(_ row: SessionRow, query: String) -> Bool {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if needle.isEmpty { return true }
        return [label(row), row.sessionId, row.path]
            .contains { $0.lowercased().contains(needle) }
    }

    /// The first non-empty line of a title, trimmed; nil when it has none.
    ///
    /// The service collapses whitespace, so a title it wrote is one line. A file
    /// edited by hand is not, and a sidebar row is not a paragraph.
    static func firstLine(_ text: String) -> String? {
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if !trimmed.isEmpty { return trimmed }
        }
        return nil
    }
}

/// Whether a session may be offered for deletion, given what the app is doing.
///
/// The rule is a function of three values, so it is stated here and checked
/// here; `AppModel.canDelete` is the call that reads the model's own two.
public enum SessionDeletion {
    /// A session with a run on it is not offered.
    ///
    /// The run saves its session when it ends, so a file deleted mid-run is
    /// written back a second later: the row comes back, and the user reads a
    /// Delete that did not work. The server refuses the same thing
    /// (`session_busy`) and has to — the run may be the CLI's, on a session the
    /// app is not watching — so the app's rule is the courtesy and the server's
    /// is the guarantee.
    public static func isOffered(
        _ sessionId: String, openSessionId: String?, running: Bool
    ) -> Bool {
        !(running && sessionId == openSessionId)
    }
}
