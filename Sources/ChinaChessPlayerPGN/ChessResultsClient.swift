import Foundation

actor ChessResultsClient {
    private let session: URLSession
    private let calendar = Calendar(identifier: .gregorian)

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.httpCookieStorage = .shared
        configuration.httpShouldSetCookies = true
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 60
        self.session = URLSession(configuration: configuration)
    }

    func searchPlayers(
        pinyinName: String,
        from startDate: Date,
        to endDate: Date,
        includeLikelyTestEvents: Bool
    ) async throws -> [PlayerCandidate] {
        let variants = nameVariants(for: pinyinName)
        var rowsByID: [String: TournamentEvent] = [:]

        for variant in variants {
            let rows = try await searchPlayerRows(
                lastName: variant.last,
                firstName: variant.first,
                fideID: nil,
                from: startDate,
                to: endDate,
                includeLikelyTestEvents: includeLikelyTestEvents
            )
            for row in rows {
                rowsByID[row.id] = row
            }
        }

        let seededCandidates = candidates(from: Array(rowsByID.values))
        var enrichedCandidates: [PlayerCandidate] = []
        for candidate in seededCandidates.prefix(20) {
            guard let fideID = candidate.fideID else {
                enrichedCandidates.append(candidate)
                continue
            }
            let refreshedEvents = try await searchPlayerRows(
                lastName: "",
                firstName: "",
                fideID: fideID,
                from: startDate,
                to: endDate,
                includeLikelyTestEvents: includeLikelyTestEvents
            )
            enrichedCandidates.append(candidates(from: refreshedEvents).first ?? candidate)
        }

        if seededCandidates.count > 20 {
            enrichedCandidates.append(contentsOf: seededCandidates.dropFirst(20))
        }

        return enrichedCandidates.sorted {
            ($0.latestEventDate ?? .distantPast) > ($1.latestEventDate ?? .distantPast)
        }
    }

    func searchEvents(
        fideID: String,
        from startDate: Date,
        to endDate: Date,
        includeLikelyTestEvents: Bool
    ) async throws -> [TournamentEvent] {
        try await searchPlayerRows(
            lastName: "",
            firstName: "",
            fideID: fideID,
            from: startDate,
            to: endDate,
            includeLikelyTestEvents: includeLikelyTestEvents
        )
        .sorted { lhs, rhs in
            (lhs.endDate ?? .distantPast) > (rhs.endDate ?? .distantPast)
        }
    }

    func downloadPGN(for event: TournamentEvent) async throws -> String {
        guard let fideID = event.fideID, !fideID.isEmpty else {
            throw ChessResultsError.missingFIDEID
        }

        let formURL = URL(string: "https://chess-results.com/PartieSuche.aspx?lan=1")!
        let form = try await loadForm(formURL)
        var fields = form.fields
        fields["ctl00$P1$Txt_FideID"] = fideID
        fields["ctl00$P1$txt_dbkey"] = event.tournamentID
        fields["ctl00$P1$combo_anzahl_zeilen"] = "5"
        fields["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"

        let data = try await post(form.actionURL, referer: form.baseURL, fields: fields)
        guard let text = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
            return ""
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func searchPlayerRows(
        lastName: String,
        firstName: String,
        fideID: String?,
        from startDate: Date,
        to endDate: Date,
        includeLikelyTestEvents: Bool
    ) async throws -> [TournamentEvent] {
        let formURL = URL(string: "https://chess-results.com/SpielerSuche.aspx?lan=1")!
        let form = try await loadForm(formURL)
        var fields = form.fields

        fields["ctl00$P1$txt_nachname"] = lastName
        fields["ctl00$P1$txt_vorname"] = firstName
        fields["ctl00$P1$txt_fideID"] = fideID ?? ""
        fields["ctl00$P1$txt_FED"] = fideID == nil ? "CHN" : ""
        fields["ctl00$P1$txt_von_tag"] = AppFormatters.shortDate.string(from: startDate)
        fields["ctl00$P1$txt_bis_tag"] = AppFormatters.shortDate.string(from: endDate)
        fields["ctl00$P1$combo_Sort"] = "0"
        fields["ctl00$P1$combo_anzahl_zeilen"] = "5"
        fields["ctl00$P1$cbox_FIDE"] = "on"
        fields["ctl00$P1$cb_suchen"] = "Search"

        let data = try await post(form.actionURL, referer: form.baseURL, fields: fields)
        guard let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
            throw ChessResultsError.invalidResponse
        }

        let rows = parsePlayerRows(html, baseURL: form.actionURL)
        return rows.filter { event in
            guard let eventEndDate = event.endDate else { return false }
            guard eventEndDate >= startDate && eventEndDate <= endDate else { return false }
            if !includeLikelyTestEvents && event.isLikelyTestData {
                return false
            }
            return true
        }
    }

    private func candidates(from events: [TournamentEvent]) -> [PlayerCandidate] {
        let grouped = Dictionary(grouping: events) { event in
            if let fideID = event.fideID, !fideID.isEmpty {
                return "fide-\(fideID)"
            }
            return "name-\(event.playerName.lowercased())"
        }

        return grouped.map { key, events in
            let sortedEvents = events.sorted { lhs, rhs in
                (lhs.endDate ?? .distantPast) > (rhs.endDate ?? .distantPast)
            }
            let nameVariants = orderedUnique(sortedEvents.map(\.playerName).filter { !$0.isEmpty })
            let clubs = orderedUnique(sortedEvents.map(\.club).filter { !$0.isEmpty })
            let federations = orderedUnique(sortedEvents.map(\.federation).filter { !$0.isEmpty })
            let fideIDs = orderedUnique(sortedEvents.compactMap(\.fideID).filter { !$0.isEmpty })

            return PlayerCandidate(
                id: key,
                displayName: bestDisplayName(from: nameVariants),
                fideID: fideIDs.first,
                federation: federations.first ?? "CHN",
                clubs: Array(clubs.prefix(4)),
                nameVariants: Array(nameVariants.prefix(6)),
                latestEventDate: sortedEvents.first?.endDate,
                eventCount: sortedEvents.count,
                source: "Chess-Results",
                events: sortedEvents
            )
        }
        .sorted { lhs, rhs in
            (lhs.latestEventDate ?? .distantPast) > (rhs.latestEventDate ?? .distantPast)
        }
    }

    private func parsePlayerRows(_ html: String, baseURL: URL) -> [TournamentEvent] {
        HTMLTools.tableRows(from: html).compactMap { row in
            let cells = HTMLTools.cells(from: row)
            guard cells.count >= 10 else { return nil }

            let playerLink = HTMLTools.firstLink(in: cells[0], baseURL: baseURL)
            let eventLink = HTMLTools.firstLink(in: cells[5], baseURL: baseURL)
            guard
                let eventURL = eventLink?.url,
                let tournamentID = tournamentID(from: eventURL)
            else { return nil }

            let playerURL = playerLink?.url
            let playerSerial = playerURL.flatMap(playerSerialNumber(from:))
            let playerName = HTMLTools.stripTags(cells[0]).cleanChessResultsComma()
            let fideID = HTMLTools.stripTags(cells[2]).nilIfBlank
            let club = HTMLTools.stripTags(cells[3])
            let federation = HTMLTools.stripTags(cells[4])
            let name = HTMLTools.stripTags(cells[5])
            let endDate = parseChessResultsDate(HTMLTools.stripTags(cells[6]))
            let rank = HTMLTools.stripTags(cells[7])
            let rounds = HTMLTools.stripTags(cells[8])
            let participants = HTMLTools.stripTags(cells[9])
            let likelyTest = isLikelyTestEvent(name)

            return TournamentEvent(
                id: "\(tournamentID)-\(fideID ?? playerName)-\(playerSerial ?? "")",
                tournamentID: tournamentID,
                playerSerial: playerSerial,
                playerName: playerName,
                fideID: fideID,
                club: club,
                federation: federation,
                name: name,
                endDate: endDate,
                rank: rank,
                rounds: rounds,
                participants: participants,
                eventURL: eventURL,
                playerURL: playerURL,
                source: "Chess-Results",
                isLikelyTestData: likelyTest
            )
        }
    }

    private func loadForm(_ url: URL) async throws -> WebForm {
        var request = URLRequest(url: url)
        request.setValue("Mozilla/5.0 ChinaChessPlayerPGN/1.0", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await session.data(for: request)
        guard let finalURL = response.url else { throw ChessResultsError.invalidResponse }
        guard let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
            throw ChessResultsError.invalidResponse
        }
        let actionURL = HTMLTools.formAction(from: html, baseURL: finalURL) ?? finalURL
        return WebForm(baseURL: finalURL, actionURL: actionURL, fields: HTMLTools.inputFields(from: html))
    }

    private func post(_ url: URL, referer: URL, fields: [String: String]) async throws -> Data {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("Mozilla/5.0 ChinaChessPlayerPGN/1.0", forHTTPHeaderField: "User-Agent")
        request.setValue(referer.absoluteString, forHTTPHeaderField: "Referer")
        request.httpBody = formBody(fields)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
            throw ChessResultsError.httpError
        }
        return data
    }

    private func formBody(_ fields: [String: String]) -> Data {
        let body = fields
            .map { key, value in
                "\(urlEncode(key))=\(urlEncode(value))"
            }
            .joined(separator: "&")
        return Data(body.utf8)
    }

    private func urlEncode(_ value: String) -> String {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "&+=?")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }

    private func nameVariants(for input: String) -> [(last: String, first: String)] {
        let normalized = input
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        let parts = normalized.split(separator: " ").map(String.init)
        guard !parts.isEmpty else { return [] }
        guard parts.count > 1 else { return [(parts[0], "")] }

        let last = parts[0]
        let givenWithSpaces = parts.dropFirst().joined(separator: " ")
        let givenJoined = parts.dropFirst().joined()
        var variants = [(last, givenJoined), (last, givenWithSpaces)]
        variants.append((parts.joined(separator: " "), ""))
        return orderedUnique(variants.map { "\($0.0)|\($0.1)" }).map {
            let pieces = $0.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            return (pieces.first ?? "", pieces.dropFirst().first ?? "")
        }
    }

    private func tournamentID(from url: URL) -> String? {
        let text = url.absoluteString
        guard let range = text.range(of: #"tnr(\d+)"#, options: .regularExpression) else { return nil }
        return String(text[range]).replacingOccurrences(of: "tnr", with: "")
    }

    private func playerSerialNumber(from url: URL) -> String? {
        URLComponents(url: url, resolvingAgainstBaseURL: true)?
            .queryItems?
            .first { $0.name.lowercased() == "snr" }?
            .value
    }

    private func parseChessResultsDate(_ text: String) -> Date? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.localizedCaseInsensitiveContains("unknown") else { return nil }
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        for format in ["yyyy/MM/dd", "yyyy-MM-dd", "dd.MM.yyyy"] {
            formatter.dateFormat = format
            if let date = formatter.date(from: trimmed) {
                return date
            }
        }
        return nil
    }

    private func orderedUnique<T: Hashable>(_ values: [T]) -> [T] {
        var seen: Set<T> = []
        var result: [T] = []
        for value in values where !seen.contains(value) {
            seen.insert(value)
            result.append(value)
        }
        return result
    }

    private func bestDisplayName(from variants: [String]) -> String {
        variants.first { $0.range(of: #"^[A-Za-z ,.'-]+$"#, options: .regularExpression) != nil } ?? variants.first ?? "未知棋手"
    }

    private func isLikelyTestEvent(_ name: String) -> Bool {
        let lowered = name.lowercased()
        let needles = [
            "test ",
            " test",
            "testing",
            "examen",
            "practi",
            "seminar"
        ]
        return needles.contains { lowered.contains($0) }
    }
}

private struct WebForm {
    let baseURL: URL
    let actionURL: URL
    let fields: [String: String]
}

enum ChessResultsError: LocalizedError {
    case invalidResponse
    case httpError
    case missingFIDEID

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            "无法解析 Chess-Results 返回内容"
        case .httpError:
            "Chess-Results 请求失败"
        case .missingFIDEID:
            "该赛事行缺少 FIDE ID，不能按棋手下载 PGN"
        }
    }
}

private extension String {
    var nilIfBlank: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func cleanChessResultsComma() -> String {
        trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: ","))
    }
}
