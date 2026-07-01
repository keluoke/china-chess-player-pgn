import CryptoKit
import Foundation

final class StaticBulkRepository {
    private let fileManager = FileManager.default
    private var dataRootURL: URL?
    private var bulkManifest: BulkManifest?
    private var youthManifest: BulkYouthManifest?
    private var byPlayerManifest: StaticByPlayerManifest?
    private var playerDetailCache: [String: StaticPlayerPGNDetail] = [:]
    private var indexCache: [String: [BulkYouthGameIndexEntry]] = [:]

    init() {
        self.dataRootURL = Self.findDataRootURL()
    }

    func stats() throws -> BulkDataStats {
        guard let dataRootURL else {
            return BulkDataStats()
        }

        let bulk = try loadBulkManifest()
        let youth = try loadYouthManifest()
        let byPlayer = try? loadByPlayerManifest()
        let source = bulk.sources.first
        return BulkDataStats(
            isAvailable: true,
            source: source?.source ?? youth.source,
            license: source?.license ?? youth.license,
            rootURL: dataRootURL,
            mirroredGames: bulk.totals.mirroredGames ?? bulk.totals.games,
            mirroredShards: bulk.totals.mirroredShards ?? bulk.totals.shards,
            mirroredBytes: bulk.totals.mirroredBytes,
            youthGames: youth.totals.games,
            youthPlayers: youth.totals.players,
            youthStages: youth.stages.map {
                BulkYouthStagePack(
                    id: $0.id,
                    lowerAge: $0.lowerAge,
                    upperAge: $0.upperAge,
                    birthYears: $0.birthYears,
                    games: $0.games,
                    players: $0.players,
                    pgnPath: $0.pgnPath,
                    indexPath: $0.indexPath
                )
            },
            byPlayerPlayers: byPlayer?.totals.players ?? 0,
            byPlayerGames: byPlayer?.totals.games ?? 0,
            byPlayerPackages: byPlayer?.totals.packages ?? 0
        )
    }

    func playerSummary(fideID: String?) throws -> [BulkPlayerYouthStageSummary] {
        guard dataRootURL != nil else { return [] }
        guard let fideID = fideID?.trimmingCharacters(in: .whitespacesAndNewlines), !fideID.isEmpty else {
            return []
        }
        if let detail = try? loadPlayerDetail(fideID: fideID) {
            let stagePackages = detail.packages.filter { $0.id != "all" && !$0.id.isEmpty }
            if !stagePackages.isEmpty {
                return stagePackages.map {
                    BulkPlayerYouthStageSummary(
                        stageID: $0.id,
                        games: $0.gameCount,
                        pgnPath: $0.pgnPath,
                        indexPath: "data/index/by-player/fide-\(fideID).json"
                    )
                }
            }
        }
        let youth = try loadYouthManifest()
        var summaries: [BulkPlayerYouthStageSummary] = []
        for stage in youth.stages {
            let games = try entries(for: stage).filter { $0.fideID == fideID }
            guard !games.isEmpty else { continue }
            summaries.append(
                BulkPlayerYouthStageSummary(
                    stageID: stage.id,
                    games: games.count,
                    pgnPath: stage.pgnPath,
                    indexPath: stage.indexPath
                )
            )
        }
        return summaries
    }

    func pgnForPlayer(fideID: String?, displayName: String) throws -> String {
        guard dataRootURL != nil else { return "" }
        guard let fideID = fideID?.trimmingCharacters(in: .whitespacesAndNewlines), !fideID.isEmpty else {
            return ""
        }
        if let detail = try? loadPlayerDetail(fideID: fideID),
           let package = detail.packages.first(where: { $0.id == "all" }) ?? detail.packages.first,
           !package.pgnPath.isEmpty {
            let url = url(forManifestPath: package.pgnPath)
            if fileManager.fileExists(atPath: url.path) {
                return try String(contentsOf: url, encoding: .utf8)
            }
        }

        let youth = try loadYouthManifest()
        var sections: [String] = []
        sections.append("""
        % Extracted from local bulk youth PGN
        % Player: \(displayName)
        % FIDE: \(fideID)
        % Source: \(youth.source)
        % License: \(youth.license)
        % Created: \(ISO8601DateFormatter().string(from: Date()))

        """)

        var seenHashes: Set<String> = []
        for stage in youth.stages {
            let playerEntries = try entries(for: stage).filter { $0.fideID == fideID }
            guard !playerEntries.isEmpty else { continue }

            let pgnURL = url(forManifestPath: stage.pgnPath)
            let pgnText = try String(contentsOf: pgnURL, encoding: .utf8)
            let games = PGNTools.splitGames(pgnText)
            let gamesByKey = Dictionary(grouping: games) { game in
                Self.gameKey(headers: PGNTools.headers(in: game))
            }

            for entry in playerEntries {
                let key = Self.gameKey(entry: entry)
                let matchedGame = gamesByKey[key]?.first ?? games.first { game in
                    Self.isLooseMatch(headers: PGNTools.headers(in: game), entry: entry)
                }
                guard let matchedGame else { continue }
                let hash = Self.sha256Hex(matchedGame)
                guard seenHashes.insert(hash).inserted else { continue }
                sections.append("""
                % BulkStage: \(stage.id)
                % BulkSourceShard: \(entry.sourceShard)

                \(matchedGame)

                """)
            }
        }

        return sections.joined(separator: "\n")
    }

    func stagePGNURL(for stage: BulkYouthStagePack) -> URL? {
        guard dataRootURL != nil else { return nil }
        let url = url(forManifestPath: stage.pgnPath)
        return fileManager.fileExists(atPath: url.path) ? url : nil
    }

    private func loadBulkManifest() throws -> BulkManifest {
        if let bulkManifest { return bulkManifest }
        let manifest: BulkManifest = try decodeJSON(at: url(forManifestPath: "data/bulk/manifest.json"))
        bulkManifest = manifest
        return manifest
    }

    private func loadYouthManifest() throws -> BulkYouthManifest {
        if let youthManifest { return youthManifest }
        let manifest: BulkYouthManifest = try decodeJSON(at: url(forManifestPath: "data/bulk/youth/manifest.json"))
        youthManifest = manifest
        return manifest
    }

    private func loadByPlayerManifest() throws -> StaticByPlayerManifest {
        if let byPlayerManifest { return byPlayerManifest }
        let manifest: StaticByPlayerManifest = try decodeJSON(at: url(forManifestPath: "data/index/by-player/manifest.json"))
        byPlayerManifest = manifest
        return manifest
    }

    private func loadPlayerDetail(fideID: String) throws -> StaticPlayerPGNDetail? {
        if let cached = playerDetailCache[fideID] { return cached }
        let path = "data/index/by-player/fide-\(fideID).json"
        let url = url(forManifestPath: path)
        guard fileManager.fileExists(atPath: url.path) else { return nil }
        let detail: StaticPlayerPGNDetail = try decodeJSON(at: url)
        playerDetailCache[fideID] = detail
        return detail
    }

    private func entries(for stage: BulkYouthManifest.Stage) throws -> [BulkYouthGameIndexEntry] {
        if let cached = indexCache[stage.id] { return cached }
        let entries: [BulkYouthGameIndexEntry] = try decodeJSON(at: url(forManifestPath: stage.indexPath))
        indexCache[stage.id] = entries
        return entries
    }

    private func decodeJSON<T: Decodable>(at url: URL) throws -> T {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func url(forManifestPath path: String) -> URL {
        let cleanPath = path.hasPrefix("data/") ? String(path.dropFirst(5)) : path
        return dataRootURL!.appendingPathComponent(cleanPath)
    }

    private static func findDataRootURL() -> URL? {
        let bundleCandidates = [
            Bundle.main.resourceURL?.appendingPathComponent("data", isDirectory: true),
            Bundle.main.resourceURL?.appendingPathComponent("docs/data", isDirectory: true)
        ].compactMap { $0 }

        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
        let pathCandidates = [
            cwd.appendingPathComponent("docs/data", isDirectory: true),
            cwd.appendingPathComponent("data", isDirectory: true)
        ]

        let executableAncestors = sequence(first: Bundle.main.bundleURL) { url in
            let parent = url.deletingLastPathComponent()
            return parent.path == url.path ? nil : parent
        }
        .prefix(8)
        .flatMap { url in
            [
                url.appendingPathComponent("Contents/Resources/data", isDirectory: true),
                url.appendingPathComponent("docs/data", isDirectory: true),
                url.appendingPathComponent("data", isDirectory: true)
            ]
        }

        let cwdAncestors = sequence(first: cwd) { url in
            let parent = url.deletingLastPathComponent()
            return parent.path == url.path ? nil : parent
        }
        .prefix(8)
        .flatMap { url in
            [
                url.appendingPathComponent("docs/data", isDirectory: true),
                url.appendingPathComponent("data", isDirectory: true)
            ]
        }

        for candidate in bundleCandidates + pathCandidates + executableAncestors + cwdAncestors {
            if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("bulk/manifest.json").path) {
                return candidate
            }
        }
        return nil
    }

    private static func gameKey(entry: BulkYouthGameIndexEntry) -> String {
        [
            normalize(entry.event),
            dateKey(entry.date),
            normalize(entry.white),
            normalize(entry.black),
            normalize(entry.result)
        ].joined(separator: "|")
    }

    private static func gameKey(headers: [String: String]) -> String {
        [
            normalize(headers["Event"] ?? ""),
            dateKey(headers["Date"] ?? ""),
            normalize(headers["White"] ?? ""),
            normalize(headers["Black"] ?? ""),
            normalize(headers["Result"] ?? "")
        ].joined(separator: "|")
    }

    private static func isLooseMatch(headers: [String: String], entry: BulkYouthGameIndexEntry) -> Bool {
        normalize(headers["Event"] ?? "") == normalize(entry.event)
            && dateKey(headers["Date"] ?? "") == dateKey(entry.date)
            && normalize(headers["White"] ?? "").contains(normalize(entry.white))
            && normalize(headers["Black"] ?? "").contains(normalize(entry.black))
    }

    private static func dateKey(_ value: String) -> String {
        value.filter(\.isNumber)
    }

    private static func normalize(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: Locale(identifier: "en_US_POSIX"))
            .lowercased()
            .replacingOccurrences(of: "[^a-z0-9\\p{Han}]+", with: "", options: .regularExpression)
    }

    private static func sha256Hex(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

private struct BulkManifest: Decodable {
    struct Source: Decodable {
        let source: String
        let license: String
    }

    struct Totals: Decodable {
        let shards: Int
        let games: Int
        let mirroredShards: Int?
        let mirroredGames: Int?
        let mirroredBytes: Int
    }

    let sources: [Source]
    let totals: Totals
}

private struct BulkYouthManifest: Decodable {
    struct Totals: Decodable {
        let games: Int
        let players: Int
    }

    struct Stage: Decodable {
        let id: String
        let lowerAge: Int
        let upperAge: Int
        let birthYears: String
        let games: Int
        let players: Int
        let pgnPath: String
        let indexPath: String
    }

    let source: String
    let license: String
    let totals: Totals
    let stages: [Stage]
}

private struct BulkYouthGameIndexEntry: Decodable {
    let fideID: String
    let event: String
    let date: String
    let white: String
    let black: String
    let result: String
    let sourceShard: String
}

private struct StaticByPlayerManifest: Decodable {
    struct Totals: Decodable {
        let players: Int
        let games: Int
        let packages: Int
    }

    let totals: Totals
}

private struct StaticPlayerPGNDetail: Decodable {
    struct Package: Decodable {
        let id: String
        let pgnPath: String
        let gameCount: Int
    }

    let packages: [Package]
}
