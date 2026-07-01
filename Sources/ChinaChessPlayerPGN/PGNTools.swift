import Foundation

enum PGNTools {
    static func gameCount(in text: String) -> Int {
        splitGames(text).count
    }

    static func mergedPGN(results: [PGNDownloadResult], player: PlayerCandidate) -> String {
        let successful = results.filter {
            switch $0.status {
            case .cached, .success:
                return !$0.pgn.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            case .empty, .failed:
                return false
            }
        }

        var sections: [String] = []
        sections.append("""
        % Merged by 中国棋手 PGN
        % Player: \(player.displayName)
        % FIDE: \(player.fideID ?? "unknown")
        % Created: \(ISO8601DateFormatter().string(from: Date()))

        """)

        for result in successful {
            let trimmed = result.pgn.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            sections.append("""
            % SourceEvent: \(result.event.name)
            % SourceURL: \(result.event.eventURL.absoluteString)

            \(trimmed)

            """)
        }

        return sections.joined(separator: "\n")
    }

    static func splitGames(_ pgn: String) -> [String] {
        let normalized = pgn.replacingOccurrences(of: "\r\n", with: "\n")
        let pattern = #"^\[Event\s+""#
        guard let regex = try? NSRegularExpression(
            pattern: pattern,
            options: [.anchorsMatchLines, .caseInsensitive]
        ) else {
            return []
        }

        let range = NSRange(normalized.startIndex..<normalized.endIndex, in: normalized)
        let matches = regex.matches(in: normalized, range: range)
        guard !matches.isEmpty else { return [] }

        return matches.enumerated().compactMap { index, match in
            guard let startRange = Range(match.range, in: normalized) else { return nil }
            let start = startRange.lowerBound
            let end: String.Index
            if index + 1 < matches.count,
               let nextRange = Range(matches[index + 1].range, in: normalized) {
                end = nextRange.lowerBound
            } else {
                end = normalized.endIndex
            }
            let trimmed = normalized[start..<end].trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    static func headers(in game: String) -> [String: String] {
        var headers: [String: String] = [:]
        let pattern = #"^\[([A-Za-z0-9_]+)\s+"(.*)"\]"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.anchorsMatchLines]) else {
            return headers
        }
        let range = NSRange(game.startIndex..<game.endIndex, in: game)
        for match in regex.matches(in: game, range: range) where match.numberOfRanges == 3 {
            guard
                let keyRange = Range(match.range(at: 1), in: game),
                let valueRange = Range(match.range(at: 2), in: game)
            else { continue }
            headers[String(game[keyRange])] = String(game[valueRange])
        }
        return headers
    }
}
