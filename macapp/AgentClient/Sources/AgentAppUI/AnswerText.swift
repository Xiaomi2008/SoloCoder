import AgentAppCore
import AppKit
import SwiftUI

// A finished answer, as one piece of text.
//
// `textSelection(.enabled)` makes each `Text` selectable by itself, so a drag stops
// at the end of the paragraph it started in, and there is no SwiftUI modifier that
// joins them. A selection can only cross what one text view holds, so a finished
// answer is one `NSTextView`, and the blocks are its paragraphs.
//
// `BlocksView` still draws an answer while it streams. Filling a text view on every
// publish is the trap `AssistantLine` describes, and nobody selects a moving target.
//
// TextKit 1, on purpose: the view is given its own layout manager because tables
// and code boxes are `NSTextBlock`s, which TextKit 2 would drop out of on first use.

struct AnswerText: NSViewRepresentable {
    let blocks: [MarkdownBlock]

    final class Coordinator {
        /// What the view holds. Filling it again would clear the user's selection,
        /// and a publish from the running turn must not do that.
        var shown: [MarkdownBlock]?
        var zoom: CGFloat = 1
        /// Heights by width. SwiftUI asks at more than one width in a layout pass
        /// (the width on offer, and the ideal width when it offers none), so one
        /// slot would measure the whole answer again on every pass.
        var measured: [CGFloat: CGFloat] = [:]
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSTextView {
        let storage = NSTextStorage()
        let layout = NSLayoutManager()
        storage.addLayoutManager(layout)
        let container = NSTextContainer(size: NSSize(width: Theme.columnWidth * context.environment.zoom, height: .greatestFiniteMagnitude))
        container.lineFragmentPadding = 0
        container.widthTracksTextView = true
        layout.addTextContainer(container)

        let view = AnswerTextView(frame: .zero, textContainer: container)
        view.isEditable = false
        view.isSelectable = true
        view.drawsBackground = false
        view.textContainerInset = .zero
        view.isVerticallyResizable = false
        view.isHorizontallyResizable = false
        view.linkTextAttributes = [.foregroundColor: NSColor.linkColor, .cursor: NSCursor.pointingHand]
        view.setAccessibilityLabel("Answer")
        return view
    }

    func updateNSView(_ view: NSTextView, context: Context) {
        let zoom = context.environment.zoom
        guard context.coordinator.shown != blocks || context.coordinator.zoom != zoom else { return }
        context.coordinator.shown = blocks
        context.coordinator.zoom = zoom
        context.coordinator.measured = [:]
        view.textStorage?.setAttributedString(AnswerTextBuilder.text(blocks).zoomed(zoom))
    }

    /// The height the text needs at the width on offer. A row is measured on every
    /// layout of the transcript, and a long answer's layout is the expensive part,
    /// so the answer is kept until the width or the text changes.
    ///
    /// Measuring lays the text out at the width asked about, and the container is
    /// put back to the view's own width afterwards. Left at the asked width — the
    /// ideal-width probe's 760 pt — the text is drawn narrower, and so taller, than
    /// the height its row was given: the end of the answer is cut off and the
    /// transcript cannot scroll to it. At a zoom of 1 the column is 760 pt and the
    /// two widths agreed, which is why it only showed zoomed in.
    func sizeThatFits(_ proposal: ProposedViewSize, nsView view: NSTextView, context: Context) -> CGSize? {
        let ideal = Theme.columnWidth * context.environment.zoom
        var width = proposal.width ?? ideal
        if !width.isFinite { width = ideal }
        if let height = context.coordinator.measured[width] {
            return CGSize(width: width, height: height)
        }
        guard let container = view.textContainer, let layout = view.layoutManager else { return nil }
        container.size = NSSize(width: width, height: .greatestFiniteMagnitude)
        layout.ensureLayout(for: container)
        let height = ceil(layout.usedRect(for: container).height)
        if view.frame.width > 0, view.frame.width != width {
            container.size = NSSize(width: view.frame.width, height: .greatestFiniteMagnitude)
        }
        context.coordinator.measured[width] = height
        return CGSize(width: width, height: height)
    }
}

/// Copies a table the way a table pastes: `string` has one line per cell, and a
/// row should be one line with tabs between its cells. The rich-text flavour keeps
/// the real table, so only the plain flavour is rewritten.
final class AnswerTextView: NSTextView {
    private static let plainTypes: [NSPasteboard.PasteboardType] = [.string, .init("NSStringPboardType")]

    override func writeSelection(to board: NSPasteboard, type: NSPasteboard.PasteboardType) -> Bool {
        guard Self.plainTypes.contains(type), let storage = textStorage else {
            return super.writeSelection(to: board, type: type)
        }
        let text = selectedRanges
            .map { AnswerTextBuilder.plainText(storage.attributedSubstring(from: $0.rangeValue)) }
            .joined(separator: "\n")
        return board.setString(text, forType: type)
    }
}

enum AnswerTextBuilder {
    private static let blockGap: CGFloat = 8
    private static let itemGap: CGFloat = 4

    static func text(_ blocks: [MarkdownBlock]) -> NSAttributedString {
        let out = NSMutableAttributedString()
        for block in blocks { append(block, to: out) }
        // The last paragraph's newline would be one more blank line on screen and
        // one more in the pasteboard.
        if out.string.hasSuffix("\n") { out.deleteCharacters(in: NSRange(location: out.length - 1, length: 1)) }
        return out
    }

    /// The text with each table row on one line. A cell's newline is what separates
    /// it from the next cell, so all but a row's last become tabs.
    static func plainText(_ text: NSAttributedString) -> String {
        let string = text.string as NSString
        var out = ""
        string.enumerateSubstrings(
            in: NSRange(location: 0, length: string.length), options: .byParagraphs
        ) { _, range, enclosing, _ in
            out += string.substring(with: range)
            guard enclosing.length > range.length else { return }
            let style = text.attribute(.paragraphStyle, at: range.location < string.length ? range.location : 0, effectiveRange: nil) as? NSParagraphStyle
            let cell = style?.textBlocks.first as? NSTextTableBlock
            let inRow = cell.map { $0.startingColumn + $0.columnSpan < $0.table.numberOfColumns } ?? false
            out += inRow ? "\t" : string.substring(with: NSRange(
                location: range.upperBound, length: enclosing.upperBound - range.upperBound
            ))
        }
        return out
    }

    /// A box around some paragraphs: a table of one cell, which draws its fill and
    /// borders where a bare `NSTextBlock` draws neither. Every line of a block
    /// shares the one cell, or each would get a box of its own.
    private static func box() -> NSTextTableBlock {
        let table = NSTextTable()
        table.numberOfColumns = 1
        table.setContentWidth(100, type: .percentageValueType)
        let cell = NSTextTableBlock(table: table, startingRow: 0, rowSpan: 1, startingColumn: 0, columnSpan: 1)
        cell.setWidth(blockGap, type: .absoluteValueType, for: .margin, edge: .maxY)
        return cell
    }

    private static func append(_ block: MarkdownBlock, to out: NSMutableAttributedString) {
        switch block {
        case .heading(let level, let text):
            let style = paragraph(after: blockGap)
            style.paragraphSpacingBefore = 2
            out.append(line(inline(text, font: headingFont(level), color: .labelColor), style))
        case .paragraph(let text):
            out.append(line(inline(text, font: font(.body), color: .labelColor), paragraph(after: blockGap)))
        case .code(let language, let text):
            appendCode(text, language: language, to: out)
        case .bullets(let items):
            appendList(items.map { ("•", $0) }, to: out)
        case .numbered(let items):
            appendList(items.map { ($0.label, $0.text) }, to: out)
        case .quote(let lines):
            appendQuote(lines, to: out)
        case .table(let rows, let alignment):
            appendTable(rows, alignment, to: out)
        case .rule:
            let rule = box()
            rule.setWidth(1, type: .absoluteValueType, for: .border, edge: .minY)
            rule.setBorderColor(Theme.edgeColor)
            let style = paragraph(after: 0)
            style.textBlocks = [rule]
            out.append(line(NSAttributedString(string: "", attributes: [.font: NSFont.systemFont(ofSize: 1)]), style))
        }
    }

    /// A fenced block: wrapped, not scrolled sideways, because there is one text
    /// view and it does not scroll. The wrap is only on the screen; what is copied
    /// has the block's own line breaks.
    private static func appendCode(_ text: String, language: String?, to out: NSMutableAttributedString) {
        let code = box()
        code.setWidth(8, type: .absoluteValueType, for: .padding)
        code.setWidth(1, type: .absoluteValueType, for: .border)
        code.setBorderColor(Theme.hairlineColor)
        code.backgroundColor = Theme.fillColor
        let style = paragraph(after: 0)
        style.textBlocks = [code]
        style.lineBreakMode = .byCharWrapping
        let mono = NSFont.monospacedSystemFont(ofSize: font(.caption1).pointSize, weight: .regular)
        let start = out.length
        out.append(line(NSAttributedString(string: text, attributes: [.font: mono, .foregroundColor: NSColor.labelColor]), style))
        Theme.highlight(out, at: start, code: text, language: language)
    }

    /// The marker in its own column, so a wrapped item's second line starts under
    /// the text and not under the bullet.
    private static func appendList(_ items: [(label: String, text: String)], to out: NSMutableAttributedString) {
        let body = font(.body)
        let widest = items.map { ($0.label as NSString).size(withAttributes: [.font: body]).width }.max() ?? 0
        let indent = ceil(max(widest, 10)) + 8
        for (index, item) in items.enumerated() {
            let style = paragraph(after: index == items.count - 1 ? blockGap : itemGap)
            style.headIndent = indent
            style.tabStops = [NSTextTab(textAlignment: .left, location: indent)]
            let row = NSMutableAttributedString(
                string: item.label + "\t",
                attributes: [.font: body, .foregroundColor: NSColor.secondaryLabelColor]
            )
            row.append(inline(item.text, font: body, color: .labelColor))
            out.append(line(row, style))
        }
    }

    private static func appendQuote(_ lines: [String], to out: NSMutableAttributedString) {
        let quote = box()
        quote.setWidth(2, type: .absoluteValueType, for: .border, edge: .minX)
        quote.setWidth(10, type: .absoluteValueType, for: .padding, edge: .minX)
        quote.setBorderColor(Theme.edgeColor)
        let style = paragraph(after: 2)
        style.textBlocks = [quote]
        for text in lines {
            out.append(line(inline(text, font: font(.body), color: .secondaryLabelColor), style))
        }
    }

    /// Hairlines between rows and no fills, as `TableView` draws them. The columns
    /// are left to the table's own layout, which shares the width out by content
    /// and wraps a long cell inside its column.
    private static func appendTable(
        _ rows: [[String]], _ alignment: [ColumnAlignment], to out: NSMutableAttributedString
    ) {
        let columns = rows.map(\.count).max() ?? 0
        guard columns > 0 else { return }
        let table = NSTextTable()
        table.numberOfColumns = columns
        table.layoutAlgorithm = .automaticLayoutAlgorithm
        table.collapsesBorders = true
        table.setContentWidth(100, type: .percentageValueType)
        let callout = font(.callout)
        for (rowIndex, row) in rows.enumerated() {
            for column in 0..<columns {
                let cell = NSTextTableBlock(
                    table: table, startingRow: rowIndex, rowSpan: 1, startingColumn: column, columnSpan: 1
                )
                cell.setWidth(4, type: .absoluteValueType, for: .padding, edge: .minY)
                cell.setWidth(4, type: .absoluteValueType, for: .padding, edge: .maxY)
                cell.setWidth(column == 0 ? 0 : 7, type: .absoluteValueType, for: .padding, edge: .minX)
                cell.setWidth(column == columns - 1 ? 0 : 7, type: .absoluteValueType, for: .padding, edge: .maxX)
                cell.setWidth(1, type: .absoluteValueType, for: .border, edge: .maxY)
                cell.setBorderColor(rowIndex == 0 ? Theme.edgeColor : Theme.hairlineColor, for: .maxY)
                if rowIndex == rows.count - 1 {
                    cell.setWidth(blockGap, type: .absoluteValueType, for: .margin, edge: .maxY)
                }
                let style = paragraph(after: 0)
                style.textBlocks = [cell]
                style.alignment = textAlignment(column < alignment.count ? alignment[column] : .leading)
                let text = column < row.count ? row[column] : ""
                let weight = rowIndex == 0
                    ? NSFont.systemFont(ofSize: callout.pointSize, weight: .semibold) : callout
                out.append(line(inline(text, font: weight, color: .labelColor), style))
            }
        }
    }

    /// One block's text, its newline, and the style across both.
    ///
    /// A newline inside the text is the model's line break, and to `NSTextView` it
    /// starts a paragraph, so the block's gap goes on its last line only: on every
    /// line it would space a list of paths out like separate blocks.
    private static func line(_ text: NSAttributedString, _ style: NSParagraphStyle) -> NSAttributedString {
        let out = NSMutableAttributedString(attributedString: text)
        out.append(NSAttributedString(string: "\n", attributes: text.length > 0 ? out.attributes(at: text.length - 1, effectiveRange: nil) : [:]))
        let inner = style.mutableCopy() as! NSMutableParagraphStyle
        inner.paragraphSpacing = 0
        out.addAttribute(.paragraphStyle, value: inner, range: NSRange(location: 0, length: out.length))
        let last = (out.string as NSString).paragraphRange(for: NSRange(location: out.length - 1, length: 0))
        out.addAttribute(.paragraphStyle, value: style, range: last)
        return out
    }

    private static func paragraph(after gap: CGFloat) -> NSMutableParagraphStyle {
        let style = NSMutableParagraphStyle()
        style.paragraphSpacing = gap
        return style
    }

    /// Inline markdown, read by the same parser and options as `markdownText`, and
    /// with the same fallback: markup the parser refuses is shown, not lost.
    private static func inline(_ text: String, font: NSFont, color: NSColor) -> NSAttributedString {
        let plain: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
        guard let parsed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) else { return NSAttributedString(string: text, attributes: plain) }

        let out = NSMutableAttributedString()
        for run in parsed.runs {
            var attributes = plain
            let intent = run.inlinePresentationIntent ?? []
            var runFont = font
            if intent.contains(.code) {
                runFont = NSFont.monospacedSystemFont(ofSize: font.pointSize, weight: .regular)
            }
            if intent.contains(.stronglyEmphasized) {
                runFont = NSFontManager.shared.convert(runFont, toHaveTrait: .boldFontMask)
            }
            if intent.contains(.emphasized) {
                runFont = NSFontManager.shared.convert(runFont, toHaveTrait: .italicFontMask)
            }
            attributes[.font] = runFont
            if intent.contains(.strikethrough) {
                attributes[.strikethroughStyle] = NSUnderlineStyle.single.rawValue
            }
            if let link = run.link { attributes[.link] = link }
            out.append(NSAttributedString(string: String(parsed[run.range].characters), attributes: attributes))
        }
        return out
    }

    private static func font(_ style: NSFont.TextStyle) -> NSFont { NSFont.preferredFont(forTextStyle: style) }

    private static func headingFont(_ level: Int) -> NSFont {
        switch level {
        case 1: NSFont.systemFont(ofSize: font(.title2).pointSize, weight: .semibold)
        case 2: NSFont.systemFont(ofSize: font(.title3).pointSize, weight: .semibold)
        case 3: font(.headline)
        default: NSFont.systemFont(ofSize: font(.subheadline).pointSize, weight: .semibold)
        }
    }

    private static func textAlignment(_ column: ColumnAlignment) -> NSTextAlignment {
        switch column {
        case .leading: .left
        case .trailing: .right
        case .center: .center
        }
    }
}
