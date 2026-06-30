import Foundation

struct FIDEPlayerProfile: Hashable {
    let fideID: String
    let name: String
    let federation: String
    let year: Int?
    let standardRating: Int?
    let rapidRating: Int?
    let blitzRating: Int?

    var candidate: PlayerCandidate {
        PlayerCandidate(
            id: "fide-\(fideID)",
            displayName: name,
            fideID: fideID,
            federation: federation,
            clubs: [],
            nameVariants: aliases,
            latestEventDate: nil,
            eventCount: 0,
            source: "FIDE",
            events: []
        )
    }

    var aliases: [String] {
        var values = [name, name.replacingOccurrences(of: ",", with: "")]
        let pieces = name
            .replacingOccurrences(of: ",", with: " ")
            .split(separator: " ")
            .map(String.init)
        if pieces.count >= 2 {
            values.append(pieces.joined(separator: " "))
            values.append((pieces.dropFirst() + pieces.prefix(1)).joined(separator: " "))
        }
        values.append(fideID)
        return values.orderedUnique()
    }
}

actor FIDEPlayerClient {
    private let session: URLSession

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 30
        self.session = URLSession(configuration: configuration)
    }

    func searchPlayers(query: String, federation: String = "CHN") async throws -> [PlayerCandidate] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }

        if trimmed.allSatisfy(\.isNumber), let profile = try await player(fideID: trimmed) {
            return federation.isEmpty || profile.federation == federation ? [profile.candidate] : []
        }

        var profilesByID: [String: FIDEPlayerProfile] = [:]
        for variant in searchVariants(for: trimmed) {
            let profiles = try await searchProfiles(query: variant)
            for profile in profiles where shouldInclude(profile: profile, query: trimmed, federation: federation) {
                profilesByID[profile.fideID] = profile
            }
        }

        return profilesByID.values
            .sorted { lhs, rhs in
                if lhs.federation == federation, rhs.federation != federation { return true }
                if lhs.federation != federation, rhs.federation == federation { return false }
                return bestRating(lhs) > bestRating(rhs)
            }
            .prefix(20)
            .map(\.candidate)
    }

    func player(fideID: String) async throws -> FIDEPlayerProfile? {
        guard let url = URL(string: "https://lichess.org/api/fide/player/\(fideID)") else { return nil }
        let data = try await load(url)
        let decoder = JSONDecoder()
        guard let payload = try? decoder.decode(FIDEPlayerPayload.self, from: data) else { return nil }
        return payload.profile
    }

    private func searchProfiles(query: String) async throws -> [FIDEPlayerProfile] {
        var components = URLComponents(string: "https://lichess.org/fide")!
        components.queryItems = [URLQueryItem(name: "q", value: query)]
        guard let url = components.url else { return [] }
        let data = try await load(url)
        guard let html = String(data: data, encoding: .utf8) else { return [] }
        return parseSearchResults(html)
    }

    private func load(_ url: URL) async throws -> Data {
        var request = URLRequest(url: url)
        request.setValue("Mozilla/5.0 ChinaChessPlayerPGN/1.0", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
            throw FIDEPlayerError.httpError
        }
        return data
    }

    private func parseSearchResults(_ html: String) -> [FIDEPlayerProfile] {
        HTMLTools.matches(pattern: #"<tr\b[^>]*class="[^"]*paginated[^"]*"[^>]*>.*?</tr>"#, in: html)
            .compactMap(parseRow)
    }

    private func parseRow(_ row: String) -> FIDEPlayerProfile? {
        let cells = HTMLTools.matches(pattern: #"<td\b[^>]*>.*?</td>"#, in: row)
        guard cells.count >= 5 else { return nil }
        guard
            let (fideID, name) = fideIDAndName(from: cells[0]),
            let federation = federation(from: cells[0])
        else {
            return nil
        }

        return FIDEPlayerProfile(
            fideID: fideID,
            name: name,
            federation: federation,
            year: nil,
            standardRating: Int(HTMLTools.stripTags(cells[1])),
            rapidRating: Int(HTMLTools.stripTags(cells[2])),
            blitzRating: Int(HTMLTools.stripTags(cells[3]))
        )
    }

    private func fideIDAndName(from cell: String) -> (String, String)? {
        let pattern = #"<a\b[^>]*href="/fide/(\d+)/[^"]*"[^>]*class="[^"]*player-intro__name[^"]*"[^>]*>(.*?)</a>"#
        guard
            let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]),
            let match = regex.firstMatch(in: cell, range: NSRange(cell.startIndex..<cell.endIndex, in: cell)),
            let idRange = Range(match.range(at: 1), in: cell),
            let nameRange = Range(match.range(at: 2), in: cell)
        else {
            return nil
        }

        let cleanName = HTMLTools.stripTags(String(cell[nameRange]))
            .replacingOccurrences(of: #"^(GM|IM|FM|CM|WGM|WIM|WFM|WCM)\s+"#, with: "", options: .regularExpression)
        return (String(cell[idRange]), cleanName)
    }

    private func federation(from cell: String) -> String? {
        let pattern = #"<img\b[^>]*class="[^"]*flag[^"]*"[^>]*title="([A-Z]{3})""#
        guard
            let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
            let match = regex.firstMatch(in: cell, range: NSRange(cell.startIndex..<cell.endIndex, in: cell)),
            let range = Range(match.range(at: 1), in: cell)
        else {
            return nil
        }
        return String(cell[range]).uppercased()
    }

    private func shouldInclude(profile: FIDEPlayerProfile, query: String, federation: String) -> Bool {
        guard federation.isEmpty || profile.federation == federation else { return false }
        let normalizedName = normalized(profile.name)
        let tokens = query
            .replacingOccurrences(of: ",", with: " ")
            .split(separator: " ")
            .map { normalized(String($0)) }
            .filter { !$0.isEmpty }
        guard !tokens.isEmpty else { return true }
        return tokens.allSatisfy { normalizedName.contains($0) }
    }

    private func searchVariants(for input: String) -> [String] {
        let normalized = input
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        let parts = normalized
            .replacingOccurrences(of: ",", with: " ")
            .split(separator: " ")
            .map(String.init)
        guard parts.count >= 2 else { return [normalized] }

        let reversed = (parts.dropFirst() + parts.prefix(1)).joined(separator: " ")
        return [normalized, parts.joined(separator: " "), reversed, "\(parts[0]), \(parts.dropFirst().joined(separator: " "))"].orderedUnique()
    }

    private func bestRating(_ profile: FIDEPlayerProfile) -> Int {
        max(profile.standardRating ?? 0, profile.rapidRating ?? 0, profile.blitzRating ?? 0)
    }

    private func normalized(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: Locale(identifier: "en_US_POSIX"))
            .lowercased()
            .replacingOccurrences(of: "[\\s,.'·，。\\-_]+", with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private struct FIDEPlayerPayload: Decodable {
    let id: Int
    let name: String
    let federation: String
    let year: Int?
    let standard: Int?
    let rapid: Int?
    let blitz: Int?

    var profile: FIDEPlayerProfile {
        FIDEPlayerProfile(
            fideID: "\(id)",
            name: name,
            federation: federation,
            year: year,
            standardRating: standard,
            rapidRating: rapid,
            blitzRating: blitz
        )
    }
}

enum FIDEPlayerError: LocalizedError {
    case httpError

    var errorDescription: String? {
        switch self {
        case .httpError:
            "查询 FIDE 棋手索引失败"
        }
    }
}

private extension Array where Element: Hashable {
    func orderedUnique() -> [Element] {
        var seen: Set<Element> = []
        var result: [Element] = []
        for value in self where !seen.contains(value) {
            seen.insert(value)
            result.append(value)
        }
        return result
    }
}
