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

    private let client = ChessResultsClient()
    private let repository = LocalChessRepository()
    private var latestMergedPGN = ""

    init() {
        refreshDatabaseStats()
    }

    var selectedCandidate: PlayerCandidate? {
        candidates.first { $0.id == selectedCandidateID }
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
                    selectedCandidateID = local.first?.id
                    selectedEventIDs = Set(local.first?.events.prefix(8).map(\.id) ?? [])
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
                selectedCandidateID = found.first?.id
                selectedEventIDs = Set(found.first?.events.prefix(8).map(\.id) ?? [])
                refreshDatabaseStats()
                statusText = found.isEmpty ? "未找到匹配棋手" : "找到 \(found.count) 个候选棋手，本地库已更新"
            } catch {
                statusText = error.localizedDescription
            }
            isSearching = false
        }
    }

    func selectCandidate(_ candidate: PlayerCandidate) {
        selectedCandidateID = candidate.id
        selectedEventIDs = Set(candidate.events.prefix(8).map(\.id))
        downloadResults = []
        latestMergedPGN = ""
        savedFileURL = nil
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
                    if let cachedPGN, !cachedPGN.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        pgn = cachedPGN
                        status = .cached
                    } else {
                        pgn = try await client.downloadPGN(for: event)
                        status = pgn.isEmpty ? .empty : .success
                        if !pgn.isEmpty {
                            _ = try repository.storePGN(pgn, event: event, player: candidate)
                            refreshDatabaseStats()
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

    func revealDatabaseFolder() {
        NSWorkspace.shared.activateFileViewerSelecting([repository.databaseLocation])
    }

    private func onlineCandidates(
        query: String,
        localCandidates: [PlayerCandidate],
        hintedFideIDs: [String],
        window: (start: Date, end: Date)
    ) async throws -> [PlayerCandidate] {
        let fideIDs = Array(Set(hintedFideIDs + localCandidates.compactMap(\.fideID))).sorted()
        if !fideIDs.isEmpty {
            var candidates: [PlayerCandidate] = []
            for fideID in fideIDs.prefix(20) {
                let events = try await client.searchEvents(
                    fideID: fideID,
                    from: window.start,
                    to: window.end,
                    includeLikelyTestEvents: includeLikelyTestEvents
                )
                let base = localCandidates.first { $0.fideID == fideID }
                let displayName = base?.displayName ?? events.first?.playerName ?? "FIDE \(fideID)"
                let federation = base?.federation ?? events.first?.federation ?? "CHN"
                let aliases = ((base?.nameVariants ?? []) + events.map(\.playerName)).filter { !$0.isEmpty }
                candidates.append(
                    PlayerCandidate(
                        id: "fide-\(fideID)",
                        displayName: displayName,
                        fideID: fideID,
                        federation: federation,
                        clubs: base?.clubs ?? [],
                        nameVariants: Array(Set(aliases)).sorted(),
                        latestEventDate: events.first?.endDate ?? base?.latestEventDate,
                        eventCount: events.count,
                        source: "Chess-Results",
                        events: events
                    )
                )
            }
            return candidates
        }

        return try await client.searchPlayers(
            pinyinName: query,
            from: window.start,
            to: window.end,
            includeLikelyTestEvents: includeLikelyTestEvents
        )
    }
}

extension UTType {
    static let pgn = UTType(filenameExtension: "pgn") ?? .plainText
}
