import AgentAppCore
import AppKit
import SwiftUI

// The answer, as the model wrote it.
//
// The block rules live in `AgentAppCore.Markdown`, where a check can reach them;
// this file only draws the blocks it is given. Two things are decided here.
//
// **Inline runs are `AttributedString(markdown:)`'s.** Bold, italics, inline
// code and links are a layer the system parser does properly and this file
// should not redo. It runs on a block's text, never on the whole answer, so a
// fenced block's `**` and `_` are never eaten — that is why the fence is a block.
//
// **A streaming answer is not parsed.** `Markdown.blocks` is O(n) in the answer
// and the answer grows per publish: parsing on every publish is O(n²) per run,
// the same trap that made the thinking stream crawl (see `ReasoningLine`). A
// 2k-character answer is ~1,300 parses of a string that is one character longer
// each time. So the stream shows plain text and the finished line shows markdown,
// parsed once. The change at the end is one re-layout of a line the model has
// already stopped writing, which is the moment the user is least sensitive to.

struct MarkdownView: View {
    let text: String

    var body: some View {
        BlocksView(blocks: Markdown.blocks(text)).textSelection(.enabled)
    }
}

/// Blocks, drawn.
///
/// Split out of `MarkdownView` because a streaming answer draws its finished
/// blocks and its in-progress tail through different views, and the finished part
/// is already parsed by the time it gets here.
struct BlocksView: View {
    let blocks: [MarkdownBlock]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // `Array` over `enumerated()` because a block has no identity of its
            // own: the same `paragraph("…")` can appear twice in one answer, and
            // a `ForEach` keyed on content would collapse them.
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                BlockView(block: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct BlockView: View {
    let block: MarkdownBlock

    // `body` in the type's own declaration: the result-builder transform is not
    // applied to a `body` in an extension, and a `switch` body in one fails with
    // "branches have mismatching types" for branches that are all Views.
    var body: some View {
        switch block {
        case .heading(let level, let text):
            HeadingView(level: level, text: text)
        case .paragraph(let text):
            inlineText(text)
        case .code(let language, let text):
            CodeView(language: language, text: text)
        case .bullets(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    LabeledItemView(label: "•", text: item)
                }
            }
        case .numbered(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    LabeledItemView(label: item.label, text: item.text)
                }
            }
        case .quote(let lines):
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    inlineText(line).foregroundStyle(.secondary)
                }
            }
            .padding(.leading, 10)
            .overlay(alignment: .leading) {
                Rectangle().fill(.tertiary).frame(width: 2)
            }
        case .table(let rows, let alignment):
            TableView(rows: rows, alignment: alignment)
        case .rule:
            Divider()
        }
    }

    @ViewBuilder
    private func inlineText(_ text: String) -> some View {
        markdownText(text)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A run of inline markdown — `**bold**`, `` `code` ``, a link — as a `Text`.
///
/// Falls back to the literal string when the system parser refuses it: it refuses
/// on malformed markup, and the answer is the product, so the fallback shows the
/// markup rather than nothing.
///
/// Not `Text(.init(text))`, which is the usual idiom and is not this: that
/// resolves to `LocalizedStringKey`, which does no markdown at all.
func markdownText(_ text: String) -> Text {
    if let attributed = try? AttributedString(
        markdown: text,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    ) {
        return Text(attributed)
    }
    return Text(text)
}

struct HeadingView: View {
    let level: Int
    let text: String

    var body: some View {
        Group {
            switch level {
            case 1:
                styled(.title2).fontWeight(.semibold)
            case 2:
                styled(.title3).fontWeight(.semibold)
            case 3:
                styled(.headline)
            default:
                styled(.subheadline).fontWeight(.semibold)
            }
        }
        .padding(.top, 2)
    }

    private func styled(_ font: Font) -> Text {
        markdownText(text).font(font)
    }
}

/// A fenced block, verbatim.
///
/// Wide lines scroll sideways instead of wrapping: a code sample whose alignment
/// carries meaning (a diff, a table of columns, an indented block) is destroyed
/// by wrapping, and a wrapped one cannot be copied back out.
struct CodeView: View {
    let language: String?
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let language, !language.isEmpty {
                Text(language)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 8)
                    .padding(.top, 6)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                Text(text)
                    .font(.system(.caption, design: .monospaced))
                    .fixedSize(horizontal: true, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
        .overlay(alignment: .topTrailing) {
            CopyButton(text: text, label: "this block").padding(4)
        }
    }
}

/// One list item: the marker in its own column, so a wrapped item's second line
/// starts under the text and not under the bullet.
struct LabeledItemView: View {
    let label: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Text(label)
                .font(.body)
                .foregroundStyle(.secondary)
                .frame(minWidth: 16, alignment: .trailing)
            markdownText(text)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A pipe table.
///
/// Rendered as a grid, not as the model's own aligned text: the pipes line up
/// only in a monospaced font, and the answer is not set in one.
///
/// Three things the grid needs that the markup does not give it:
///
/// - **The columns follow the delimiter row's colons.** `---:` is the model
///   saying a column is a count, and a left-aligned column of counts does not
///   read as one. The parser read them (`MarkdownBlock.table`'s `alignment`);
///   this is where they become an `Alignment`.
/// - **A hairline under every row, not only under the header.** In a
///   proportional font a row of cells with nothing between them reads as a wide
///   paragraph, not as rows you can follow across. The header's rule is the
///   darker of the theme's two hairlines, so the header still separates from the
///   body.
/// - **A cell is capped.** One long cell — a path, a sentence — would widen its
///   column and push the rest of the table out of the reading column; capped, it
///   wraps inside its own column.
///
/// No fills. Bionic separates with hairlines at 4–12% alpha and adds no grey
/// rectangle, and a filled header is a second border doing the work of one rule.
struct TableView: View {
    let rows: [[String]]
    let alignment: [ColumnAlignment]

    /// How wide one cell may get before it wraps. Four of these plus the spacing
    /// fill the 760 pt reading column, which is the widest table a model writes
    /// with a straight face; a fifth column shares what is left.
    private static let cellCap: CGFloat = 220

    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                GridRow {
                    ForEach(Array(row.enumerated()), id: \.offset) { column, cell in
                        markdownText(cell)
                            .fontWeight(index == 0 ? .semibold : .regular)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: Self.cellCap, alignment: Alignment(horizontal: align(column), vertical: .top))
                            .gridColumnAlignment(align(column))
                            .padding(.vertical, 4)
                    }
                }
                // A 1pt rule, not a `Divider`. A `Divider` carries its own
                // vertical padding, and in a grid row that padding is the row's
                // height: a single-line row measured 40 pt, which is a form
                // field, not a row of a table.
                Rectangle()
                    .fill(index == 0 ? Theme.edge : Theme.hairline)
                    .frame(height: 1)
                    .gridCellUnsizedAxes(.horizontal)
            }
        }
        // The grid takes the reading column's width, and a `Grid` divides the
        // surplus among its columns, so the rules run the full 760 pt and a short
        // first column ends up wide. That is the width of a data table, and it is
        // what GitHub does with a markdown table; the alternative is not taken.
        // `fixedSize` on a `Grid` — either axis — reports the height of a
        // single-line layout while its cells wrap, and the block under the table
        // draws over its last row. Measured, not theorised: the capture shows the
        // overlap.
        .font(.callout)
        .padding(.vertical, 2)
    }

    /// The column's alignment, falling back to leading for a table whose rows
    /// are wider than its delimiter row — which `Markdown` squares, so only a
    /// hand-built block can reach it.
    ///
    /// A `HorizontalAlignment`, because that is what `gridColumnAlignment`
    /// takes; the frame wraps it in an `Alignment` for its own side.
    private func align(_ column: Int) -> HorizontalAlignment {
        guard column < alignment.count else { return .leading }
        switch alignment[column] {
        case .leading: return .leading
        case .trailing: return .trailing
        case .center: return .center
        }
    }
}
