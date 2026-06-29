import Foundation

enum HTMLTools {
    static func decode(_ value: String) -> String {
        var result = value
            .replacingOccurrences(of: "&nbsp;", with: " ")
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&apos;", with: "'")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&ndash;", with: "-")
            .replacingOccurrences(of: "&mdash;", with: "-")
            .replacingOccurrences(of: "&frac12;", with: "1/2")

        result = replacingNumericEntities(in: result, radix: 10, prefix: "&#", suffix: ";")
        result = replacingNumericEntities(in: result, radix: 16, prefix: "&#x", suffix: ";")
        result = replacingNumericEntities(in: result, radix: 16, prefix: "&#X", suffix: ";")
        return result
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func stripTags(_ html: String) -> String {
        let withoutTags = html.replacingOccurrences(of: "<[^>]+>", with: " ", options: .regularExpression)
        return decode(withoutTags)
    }

    static func attribute(_ name: String, in html: String) -> String? {
        let pattern = #"\#(name)\s*=\s*"([^"]*)""#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return nil }
        let range = NSRange(html.startIndex..<html.endIndex, in: html)
        guard let match = regex.firstMatch(in: html, range: range), match.numberOfRanges > 1 else { return nil }
        return rangeString(match.range(at: 1), in: html).map(decode)
    }

    static func inputFields(from html: String) -> [String: String] {
        var fields: [String: String] = [:]
        for tag in matches(pattern: #"<input\b[^>]*>"#, in: html) {
            guard let name = attribute("name", in: tag) else { continue }
            let type = attribute("type", in: tag)?.lowercased() ?? "text"
            if ["submit", "button", "image", "reset"].contains(type) {
                continue
            }
            if ["checkbox", "radio"].contains(type), !tag.localizedCaseInsensitiveContains("checked") {
                continue
            }
            fields[name] = attribute("value", in: tag) ?? ""
        }

        let selectPattern = #"<select\b[^>]*name="([^"]+)"[^>]*>(.*?)</select>"#
        guard let regex = try? NSRegularExpression(pattern: selectPattern, options: [.caseInsensitive, .dotMatchesLineSeparators]) else {
            return fields
        }
        let range = NSRange(html.startIndex..<html.endIndex, in: html)
        for match in regex.matches(in: html, range: range) {
            guard
                let name = rangeString(match.range(at: 1), in: html).map(decode),
                let body = rangeString(match.range(at: 2), in: html)
            else { continue }
            fields[name] = selectedOptionValue(from: body)
        }
        return fields
    }

    static func formAction(from html: String, baseURL: URL) -> URL? {
        guard
            let formTag = matches(pattern: #"<form\b[^>]*>"#, in: html).first,
            let action = attribute("action", in: formTag)
        else {
            return nil
        }
        return URL(string: action, relativeTo: baseURL)?.absoluteURL
    }

    static func tableRows(from html: String) -> [String] {
        matches(pattern: #"<tr\b[^>]*class="[^"]*CRg[12][^"]*"[^>]*>.*?</tr>"#, in: html)
    }

    static func cells(from rowHTML: String) -> [String] {
        matches(pattern: #"<t[dh]\b[^>]*>.*?</t[dh]>"#, in: rowHTML)
    }

    static func firstLink(in html: String, baseURL: URL) -> (text: String, url: URL?)? {
        guard let anchor = matches(pattern: #"<a\b[^>]*>.*?</a>"#, in: html).first else { return nil }
        let text = stripTags(anchor)
        let href = attribute("href", in: anchor)
        return (text, href.flatMap { URL(string: $0, relativeTo: baseURL)?.absoluteURL })
    }

    static func matches(pattern: String, in html: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]) else {
            return []
        }
        let range = NSRange(html.startIndex..<html.endIndex, in: html)
        return regex.matches(in: html, range: range).compactMap { rangeString($0.range, in: html) }
    }

    private static func selectedOptionValue(from body: String) -> String {
        let selected = matches(pattern: #"<option\b[^>]*selected="selected"[^>]*>"#, in: body).first
        let first = matches(pattern: #"<option\b[^>]*>"#, in: body).first
        guard let option = selected ?? first else { return "" }
        return attribute("value", in: option) ?? ""
    }

    private static func replacingNumericEntities(in input: String, radix: Int, prefix: String, suffix: String) -> String {
        var output = input
        let escapedPrefix = NSRegularExpression.escapedPattern(for: prefix)
        let escapedSuffix = NSRegularExpression.escapedPattern(for: suffix)
        let digitPattern = radix == 16 ? #"([0-9a-fA-F]+)"# : #"([0-9]+)"#
        guard let regex = try? NSRegularExpression(pattern: "\(escapedPrefix)\(digitPattern)\(escapedSuffix)") else {
            return output
        }

        let matches = regex.matches(in: output, range: NSRange(output.startIndex..<output.endIndex, in: output)).reversed()
        for match in matches {
            guard
                let codeString = rangeString(match.range(at: 1), in: output),
                let scalarValue = UInt32(codeString, radix: radix),
                let scalar = UnicodeScalar(scalarValue)
            else { continue }
            let range = Range(match.range, in: output)!
            output.replaceSubrange(range, with: String(Character(scalar)))
        }
        return output
    }

    private static func rangeString(_ nsRange: NSRange, in text: String) -> String? {
        guard let range = Range(nsRange, in: text) else { return nil }
        return String(text[range])
    }
}
