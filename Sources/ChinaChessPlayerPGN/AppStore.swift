import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class AppStore: ObservableObject {
    @Published var query = ""
    @Published var includeLikelyTestEvents = false
    @Published var autoRefreshOnline = false
    @Published var candidates: [PlayerCandidate] = []
    @Published var selectedCandidateID: PlayerCandidate.ID?
    @Published var selectedEventIDs: Set<TournamentEvent.ID> = []
    @Published var downloadResults: [PGNDownloadResult] = []
    @Published var statusText = "输入棋手拼音姓名"
    @Published var isSearching = false
    @Published var isDownloading = false
    @Published var downloadProgress = 0.0
    @Published var savedFileURL: URL?
    @Published var databaseStats = DatabaseStats()
    @Published var bulkDataStats = BulkDataStats()
    @Published var recommendedYouthPlayers: [RecommendedYouthPlayer] = []
    @Published var youthLeaderboards: [YouthLeaderboard] = []
    @Published var selectedDashboardStats = PlayerDashboardStats()

    private let client = ChessResultsClient()
    private let fideClient = FIDEPlayerClient()
    private let repository = LocalChessRepository()
    private let staticBulkRepository = StaticBulkRepository()
    private var latestMergedPGN = ""

    init() {
        refreshDatabaseStats()
        loadBulkData()
        loadHomepage()
    }

    var selectedCandidate: PlayerCandidate? {
        candidates.first { $0.id == selectedCandidateID }
            ?? recommendedYouthPlayers.map(\.candidate).first { $0.id == selectedCandidateID }
            ?? youthLeaderboards.flatMap(\.entries).map(\.candidate).first { $0.id == selectedCandidateID }
    }

    var youthLeaderboardEntryCount: Int {
        youthLeaderboards.reduce(0) { $0 + $1.entries.count }
    }

    var selectedEvents: [TournamentEvent] {
        guard let candidate = selectedCandidate else { return [] }
        return candidate.events.filter { selectedEventIDs.contains($0.id) }
    }

    var dateWindow: (start: Date, end: Date) {
        let end = Date()
        let start = Calendar.current.date(byAdding: .year, value: -10, to: end) ?? end
        return (start, end)
    }

    var selectedCountText: String {
        "\(selectedEventIDs.count) / \(selectedCandidate?.events.count ?? 0)"
    }

    var successfulDownloadCount: Int {
        downloadResults.filter {
            switch $0.status {
            case .cached, .success:
                return true
            case .empty, .failed:
                return false
            }
        }.count
    }

    var totalGameCount: Int {
        downloadResults.reduce(0) { $0 + $1.gameCount }
    }

    func search() {
        let trimmedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedQuery.isEmpty else {
            statusText = "请输入棋手拼音姓名"
            return
        }

        isSearching = true
        candidates = []
        selectedCandidateID = nil
        selectedEventIDs = []
        downloadResults = []
        selectedDashboardStats = PlayerDashboardStats()
        latestMergedPGN = ""
        savedFileURL = nil
        statusText = "正在查询本地库"

        Task {
            do {
                let window = dateWindow
                let local = try repository.searchCandidates(
                    query: trimmedQuery,
                    includeLikelyTestEvents: includeLikelyTestEvents
                )
                if !local.isEmpty {
                    candidates = local
                    applyDefaultSelection(local.first)
                    refreshDatabaseStats()
                    if !autoRefreshOnline, local.contains(where: { !$0.events.isEmpty }) {
                        statusText = "本地命中 \(local.count) 个候选；未联网"
                        isSearching = false
                        return
                    }
                    statusText = "本地命中 \(local.count) 个候选，正在联网补齐"
                } else {
                    statusText = "本地未命中，正在搜索 Chess-Results"
                }

                let hintedFideIDs = try repository.fideIDsForQuery(trimmedQuery)
                let online = try await onlineCandidates(
                    query: trimmedQuery,
                    localCandidates: local,
                    hintedFideIDs: hintedFideIDs,
                    window: window
                )

                if !online.isEmpty {
                    try repository.upsert(candidates: online)
                }

                let refreshedLocal = try repository.searchCandidates(
                    query: trimmedQuery,
                    includeLikelyTestEvents: includeLikelyTestEvents
                )
                let found = refreshedLocal.isEmpty ? online : refreshedLocal
                candidates = found
                applyDefaultSelection(found.first)
                refreshDatabaseStats()
                loadHomepage()
                statusText = found.isEmpty ? "未找到匹配棋手" : "找到 \(found.count) 个候选棋手，本地库已更新"
            } catch {
                statusText = error.localizedDescription
            }
            isSearching = false
        }
    }

    func loadHomepage() {
        do {
            recommendedYouthPlayers = try repository.recommendedYouthPlayers(
                includeLikelyTestEvents: includeLikelyTestEvents
            )
            youthLeaderboards = try repository.youthLeaderboards(
                includeLikelyTestEvents: includeLikelyTestEvents
            )
        } catch {
            statusText = error.localizedDescription
        }
    }

    func showHome() {
        selectedCandidateID = nil
        selectedEventIDs = []
        selectedDashboardStats = PlayerDashboardStats()
        downloadResults = []
        latestMergedPGN = ""
        savedFileURL = nil
        statusText = "首页"
        refreshDatabaseStats()
        loadBulkData()
        loadHomepage()
    }

    func selectCandidate(_ candidate: PlayerCandidate) {
        if !candidates.contains(where: { $0.id == candidate.id }) {
            candidates.insert(candidate, at: 0)
        }
        selectedCandidateID = candidate.id
        selectedEventIDs = Set(candidate.events.prefix(8).map(\.id))
        downloadResults = []
        latestMergedPGN = ""
        savedFileURL = nil
        updateDashboard(for: candidate)
        refreshFIDEProfile(for: candidate)
        statusText = "已选择 \(candidate.displayName)"
    }

    func toggleEvent(_ event: TournamentEvent) {
        if selectedEventIDs.contains(event.id) {
            selectedEventIDs.remove(event.id)
        } else {
            selectedEventIDs.insert(event.id)
        }
        downloadResults = []
        latestMergedPGN = ""
        savedFileURL = nil
    }

    func selectAllEvents() {
        guard let candidate = selectedCandidate else { return }
        selectedEventIDs = Set(candidate.events.map(\.id))
        downloadResults = []
        latestMergedPGN = ""
    }

    func clearEventSelection() {
        selectedEventIDs = []
        downloadResults = []
        latestMergedPGN = ""
    }

    func downloadAndSavePGN() {
        guard let candidate = selectedCandidate else {
            statusText = "请先选择棋手"
            return
        }
        let events = selectedEvents
        guard !events.isEmpty else {
            statusText = "请先选择赛事"
            return
        }

        isDownloading = true
        downloadProgress = 0
        downloadResults = []
        latestMergedPGN = ""
        savedFileURL = nil
        statusText = "正在下载 PGN"

        Task {
            var results: [PGNDownloadResult] = []
            for (index, event) in events.enumerated() {
                do {
                    let cachedPGN = try repository.cachedPGN(for: event, player: candidate)
                    let pgn: String
                    let status: PGNDownloadStatus
                    if let cachedPGN, PGNTools.gameCount(in: cachedPGN) > 0 {
                        pgn = cachedPGN
                        status = .cached
                    } else {
                        let downloadedPGN = try await client.downloadPGN(for: event)
                        if PGNTools.gameCount(in: downloadedPGN) > 0 {
                            pgn = downloadedPGN
                            status = .success
                            _ = try repository.storePGN(pgn, event: event, player: candidate)
                            refreshDatabaseStats()
                            updateDashboard(for: candidate)
                        } else {
                            pgn = ""
                            status = .empty
                        }
                    }
                    results.append(PGNDownloadResult(event: event, status: status, pgn: pgn))
                } catch {
                    results.append(PGNDownloadResult(event: event, status: .failed(error.localizedDescription), pgn: ""))
                }
                downloadResults = results
                downloadProgress = Double(index + 1) / Double(events.count)
            }

            latestMergedPGN = PGNTools.mergedPGN(results: results, player: candidate)
            isDownloading = false

            if successfulDownloadCount == 0 {
                statusText = "选中赛事没有可下载棋谱"
            } else {
                statusText = "已合并 \(totalGameCount) 盘棋"
                saveMergedPGN(for: candidate)
            }
        }
    }

    func saveMergedPGN(for candidate: PlayerCandidate? = nil) {
        let player = candidate ?? selectedCandidate
        guard let player else { return }
        guard !latestMergedPGN.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            statusText = "没有可保存的 PGN"
            return
        }

        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pgn]
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = defaultFileName(for: player)
        panel.title = "保存合并 PGN"

        let response = panel.runModal()
        guard response == .OK, let url = panel.url else { return }

        do {
            try latestMergedPGN.write(to: url, atomically: true, encoding: .utf8)
            savedFileURL = url
            statusText = "PGN 已保存"
        } catch {
            statusText = error.localizedDescription
        }
    }

    private func defaultFileName(for player: PlayerCandidate) -> String {
        let safeName = player.displayName
            .replacingOccurrences(of: "[^A-Za-z0-9\\p{Han}_-]+", with: "_", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        let stamp = AppFormatters.pgnStamp.string(from: Date())
        return "\(safeName.isEmpty ? "player" : safeName)-\(stamp).pgn"
    }

    func refreshDatabaseStats() {
        do {
            databaseStats = try repository.stats()
        } catch {
            statusText = error.localizedDescription
        }
    }

    func loadBulkData() {
        do {
            bulkDataStats = try staticBulkRepository.stats()
        } catch {
            bulkDataStats = BulkDataStats()
            statusText = "bulk 静态数据加载失败：\(error.localizedDescription)"
        }
    }

    func revealDatabaseFolder() {
        NSWorkspace.shared.activateFileViewerSelecting([repository.databaseLocation])
    }

    func revealStaticDataFolder() {
        guard let url = bulkDataStats.rootURL else {
            statusText = "未找到本地静态数据目录"
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func saveBulkYouthStagePGN(_ stage: BulkYouthStagePack) {
        guard let sourceURL = staticBulkRepository.stagePGNURL(for: stage) else {
            statusText = "未找到 \(stage.id) 青少年 PGN 包"
            return
        }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pgn]
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = "china-youth-\(stage.id)-lichess-broadcast.pgn"
        panel.title = "保存 \(stage.id) 青少年 PGN"
        guard panel.runModal() == .OK, let destinationURL = panel.url else { return }

        do {
            if FileManager.default.fileExists(atPath: destinationURL.path) {
                try FileManager.default.removeItem(at: destinationURL)
            }
            try FileManager.default.copyItem(at: sourceURL, to: destinationURL)
            savedFileURL = destinationURL
            statusText = "已保存 \(stage.id) 青少年 PGN"
        } catch {
            statusText = error.localizedDescription
        }
    }

    func saveSelectedBulkYouthPGN() {
        guard let candidate = selectedCandidate else {
            statusText = "请先选择棋手"
            return
        }

        do {
            let pgn = try staticBulkRepository.pgnForPlayer(
                fideID: candidate.fideID,
                displayName: candidate.displayName
            )
            guard PGNTools.gameCount(in: pgn) > 0 else {
                statusText = "本地 bulk 青少年包未命中该棋手"
                return
            }
            latestMergedPGN = pgn
            saveMergedPGN(for: candidate)
        } catch {
            statusText = "导出本地 bulk PGN 失败：\(error.localizedDescription)"
        }
    }

    private func applyDefaultSelection(_ candidate: PlayerCandidate?) {
        selectedCandidateID = candidate?.id
        selectedEventIDs = Set(candidate?.events.prefix(8).map(\.id) ?? [])
        if let candidate {
            updateDashboard(for: candidate)
        } else {
            selectedDashboardStats = PlayerDashboardStats()
        }
    }

    private func updateDashboard(for candidate: PlayerCandidate) {
        do {
            var stats = try repository.dashboardStats(for: candidate)
            stats.bulkYouthStages = (try? staticBulkRepository.playerSummary(fideID: candidate.fideID)) ?? []
            stats.bulkYouthGames = stats.bulkYouthStages.reduce(0) { $0 + $1.games }
            selectedDashboardStats = stats
        } catch {
            selectedDashboardStats = PlayerDashboardStats(eventCount: candidate.events.count)
            statusText = error.localizedDescription
        }
    }

    private func refreshFIDEProfile(for candidate: PlayerCandidate) {
        guard let fideID = candidate.fideID, !fideID.isEmpty else { return }
        Task {
            guard let profile = try? await fideClient.player(fideID: fideID) else { return }
            var enriched = profile.candidate
            enriched.displayName = candidate.displayName
            enriched.events = candidate.events
            enriched.latestEventDate = candidate.latestEventDate
            enriched.eventCount = candidate.eventCount
            enriched.clubs = candidate.clubs
            enriched.nameVariants = (candidate.nameVariants + enriched.nameVariants).orderedUnique()
            enriched.source = [candidate.source, "FIDE"].orderedUnique().joined(separator: " + ")

            try? repository.upsert(candidates: [enriched])
            replaceCandidate(enriched)
            if selectedCandidateID == enriched.id {
                updateDashboard(for: enriched)
            }
        }
    }

    private func replaceCandidate(_ candidate: PlayerCandidate) {
        if let index = candidates.firstIndex(where: { $0.id == candidate.id }) {
            candidates[index] = candidate
        }
        if let index = recommendedYouthPlayers.firstIndex(where: { $0.candidate.id == candidate.id }) {
            recommendedYouthPlayers[index] = RecommendedYouthPlayer(
                seed: recommendedYouthPlayers[index].seed,
                candidate: candidate,
                dashboard: (try? repository.dashboardStats(for: candidate)) ?? recommendedYouthPlayers[index].dashboard
            )
        }
        loadHomepage()
    }

    private func onlineCandidates(
        query: String,
        localCandidates: [PlayerCandidate],
        hintedFideIDs: [String],
        window: (start: Date, end: Date)
    ) async throws -> [PlayerCandidate] {
        let trimmedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let explicitFideID = trimmedQuery.allSatisfy(\.isNumber) ? trimmedQuery : nil
        let fideIDs = Array(Set(hintedFideIDs + localCandidates.compactMap(\.fideID) + [explicitFideID].compactMap { $0 })).sorted()
        var candidatesByID: [PlayerCandidate.ID: PlayerCandidate] = [:]
        var chessResultsError: Error?

        if !fideIDs.isEmpty {
            for fideID in fideIDs.prefix(20) {
                let events = (try? await client.searchEvents(
                    fideID: fideID,
                    from: window.start,
                    to: window.end,
                    includeLikelyTestEvents: includeLikelyTestEvents
                )) ?? []
                let profile = try? await fideClient.player(fideID: fideID)
                let base = localCandidates.first { $0.fideID == fideID }
                merge(
                    PlayerCandidate(
                        id: "fide-\(fideID)",
                        displayName: base?.displayName ?? profile?.name ?? events.first?.playerName ?? "FIDE \(fideID)",
                        fideID: fideID,
                        federation: base?.federation ?? profile?.federation ?? events.first?.federation ?? "CHN",
                        birthYear: base?.birthYear ?? profile?.year,
                        standardRating: base?.standardRating ?? profile?.standardRating,
                        rapidRating: base?.rapidRating ?? profile?.rapidRating,
                        blitzRating: base?.blitzRating ?? profile?.blitzRating,
                        clubs: base?.clubs ?? [],
                        nameVariants: Array(((base?.nameVariants ?? []) + (profile?.aliases ?? []) + events.map(\.playerName)).filter { !$0.isEmpty }.orderedUnique().prefix(12)),
                        latestEventDate: events.first?.endDate ?? base?.latestEventDate,
                        eventCount: events.count,
                        source: profile == nil ? "Chess-Results" : "Chess-Results + FIDE",
                        events: events,
                        fideRatingHistory: profile?.ratingHistory ?? base?.fideRatingHistory ?? []
                    ),
                    into: &candidatesByID
                )
            }
        }

        do {
            let chessResultsCandidates = try await client.searchPlayers(
                pinyinName: query,
                from: window.start,
                to: window.end,
                includeLikelyTestEvents: includeLikelyTestEvents
            )
            for candidate in chessResultsCandidates {
                merge(candidate, into: &candidatesByID)
            }
        } catch {
            chessResultsError = error
        }

        let fideCandidates: [PlayerCandidate]
        do {
            fideCandidates = try await fideClient.searchPlayers(query: query, federation: "CHN")
        } catch {
            if candidatesByID.isEmpty {
                throw chessResultsError ?? error
            }
            fideCandidates = []
        }

        for fideCandidate in fideCandidates {
            let events: [TournamentEvent]
            if let fideID = fideCandidate.fideID {
                events = (try? await client.searchEvents(
                    fideID: fideID,
                    from: window.start,
                    to: window.end,
                    includeLikelyTestEvents: includeLikelyTestEvents
                )) ?? []
            } else {
                events = []
            }
            var enriched = fideCandidate
            enriched.events = events
            enriched.latestEventDate = events.first?.endDate
            enriched.eventCount = events.count
            enriched.source = events.isEmpty ? "FIDE" : "Chess-Results + FIDE"
            enriched.clubs = events.map(\.club).filter { !$0.isEmpty }.orderedUnique()
            enriched.nameVariants = (fideCandidate.nameVariants + events.map(\.playerName)).orderedUnique()
            merge(enriched, into: &candidatesByID)
        }

        let result = candidatesByID.values.sorted {
            if ($0.latestEventDate ?? .distantPast) != ($1.latestEventDate ?? .distantPast) {
                return ($0.latestEventDate ?? .distantPast) > ($1.latestEventDate ?? .distantPast)
            }
            if $0.eventCount != $1.eventCount {
                return $0.eventCount > $1.eventCount
            }
            return $0.displayName.localizedStandardCompare($1.displayName) == .orderedAscending
        }

        if result.isEmpty, let chessResultsError {
            throw chessResultsError
        }
        return result
    }

    private func merge(_ candidate: PlayerCandidate, into candidatesByID: inout [PlayerCandidate.ID: PlayerCandidate]) {
        guard let existing = candidatesByID[candidate.id] else {
            candidatesByID[candidate.id] = candidate
            return
        }

        let events = (existing.events + candidate.events)
            .reduce(into: [TournamentEvent.ID: TournamentEvent]()) { partialResult, event in
                partialResult[event.id] = event
            }
            .values
            .sorted { ($0.endDate ?? .distantPast) > ($1.endDate ?? .distantPast) }

        candidatesByID[candidate.id] = PlayerCandidate(
            id: candidate.id,
            displayName: existing.displayName.hasPrefix("FIDE ") ? candidate.displayName : existing.displayName,
            fideID: existing.fideID ?? candidate.fideID,
            federation: existing.federation.isEmpty ? candidate.federation : existing.federation,
            birthYear: existing.birthYear ?? candidate.birthYear,
            standardRating: existing.standardRating ?? candidate.standardRating,
            rapidRating: existing.rapidRating ?? candidate.rapidRating,
            blitzRating: existing.blitzRating ?? candidate.blitzRating,
            clubs: (existing.clubs + candidate.clubs).orderedUnique(),
            nameVariants: (existing.nameVariants + candidate.nameVariants).orderedUnique(),
            latestEventDate: events.first?.endDate ?? existing.latestEventDate ?? candidate.latestEventDate,
            eventCount: events.count,
            source: [existing.source, candidate.source].orderedUnique().joined(separator: " + "),
            events: events,
            fideRatingHistory: existing.fideRatingHistory.isEmpty ? candidate.fideRatingHistory : existing.fideRatingHistory
        )
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

extension UTType {
    static let pgn = UTType(filenameExtension: "pgn") ?? .plainText
}
