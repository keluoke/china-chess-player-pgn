import CryptoKit
import Foundation
import SQLite3

final class LocalChessRepository {
    private let databaseURL: URL
    private let archiveRootURL: URL
    private var db: OpaquePointer?
    private var initializationError: Error?

    init() {
        let supportURL: URL
        do {
            supportURL = try Self.applicationSupportURL()
        } catch {
            supportURL = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("ChinaChessPlayerPGN", isDirectory: true)
            self.initializationError = error
        }

        self.databaseURL = supportURL.appendingPathComponent("china-chess-player-pgn.sqlite")
        self.archiveRootURL = supportURL.appendingPathComponent("PGNArchive", isDirectory: true)

        do {
            try FileManager.default.createDirectory(at: archiveRootURL, withIntermediateDirectories: true)

            var handle: OpaquePointer?
            if sqlite3_open(databaseURL.path, &handle) == SQLITE_OK {
                self.db = handle
                try execute("PRAGMA foreign_keys = ON;")
                try execute("PRAGMA journal_mode = WAL;")
                try prepareSchema()
                try migrateSchema()
                try seedChinesePlayers()
            } else {
                throw SQLiteStoreError.openFailed(String(cString: sqlite3_errmsg(handle)))
            }
        } catch {
            self.initializationError = error
        }
    }

    deinit {
        if let db {
            sqlite3_close(db)
        }
    }

    func searchCandidates(query: String, includeLikelyTestEvents: Bool) throws -> [PlayerCandidate] {
        try ensureReady()
        let normalized = Self.normalizedAlias(query)
        guard !normalized.isEmpty else { return [] }

        let rows = try select(
            """
            SELECT DISTINCT p.id, p.fide_id, p.chinese_name, p.pinyin_name, p.english_name, p.federation,
                   p.birth_year, p.standard_rating, p.rapid_rating, p.blitz_rating
            FROM players p
            JOIN player_aliases a ON a.player_id = p.id
            WHERE a.normalized_alias = ? OR a.normalized_alias LIKE ?
            ORDER BY CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END, p.english_name
            LIMIT 40
            """,
            [normalized, "\(normalized)%", normalized]
        )

        return try rows.map { row in
            try candidate(from: row, includeLikelyTestEvents: includeLikelyTestEvents)
        }
    }

    func recommendedYouthPlayers(includeLikelyTestEvents: Bool) throws -> [RecommendedYouthPlayer] {
        try ensureReady()
        return try RecommendedYouthSeeds.players.compactMap { seed in
            guard let candidate = try candidate(
                for: "fide-\(seed.fideID)",
                includeLikelyTestEvents: includeLikelyTestEvents
            ) else {
                return nil
            }
            return RecommendedYouthPlayer(
                seed: seed,
                candidate: candidate,
                dashboard: try dashboardStats(for: candidate)
            )
        }
    }

    func youthLeaderboards(includeLikelyTestEvents: Bool, limit: Int = 5) throws -> [YouthLeaderboard] {
        try ensureReady()
        let rows = try select(
            """
            SELECT id, fide_id, chinese_name, pinyin_name, english_name, federation,
                   birth_year, standard_rating, rapid_rating, blitz_rating
            FROM players
            WHERE birth_year IS NOT NULL
              AND (standard_rating IS NOT NULL OR rapid_rating IS NOT NULL OR blitz_rating IS NOT NULL)
            """
        )

        var entriesByStage: [YouthStage: [YouthLeaderboardEntry]] = [:]
        for row in rows {
            let candidate = try candidate(from: row, includeLikelyTestEvents: includeLikelyTestEvents)
            guard
                let stage = Self.currentYouthStage(birthYear: candidate.birthYear),
                let rating = Self.leaderboardRating(for: candidate)
            else { continue }

            entriesByStage[stage, default: []].append(
                YouthLeaderboardEntry(
                    stage: stage,
                    rank: 0,
                    candidate: candidate,
                    rating: rating.value,
                    ratingKind: rating.kind,
                    note: Self.liChengzhiTopThreeNote(for: candidate, stage: stage)
                )
            )
        }

        return YouthStage.allCases.map { stage in
            let sortedEntries = (entriesByStage[stage] ?? [])
                .sorted {
                    if $0.rating != $1.rating { return $0.rating > $1.rating }
                    if $0.ratingKind.sortPriority != $1.ratingKind.sortPriority {
                        return $0.ratingKind.sortPriority < $1.ratingKind.sortPriority
                    }
                    return $0.candidate.displayName.localizedStandardCompare($1.candidate.displayName) == .orderedAscending
                }
                .prefix(limit)
                .enumerated()
                .map { offset, entry in
                    YouthLeaderboardEntry(
                        stage: entry.stage,
                        rank: offset + 1,
                        candidate: entry.candidate,
                        rating: entry.rating,
                        ratingKind: entry.ratingKind,
                        note: entry.note
                    )
                }

            return YouthLeaderboard(stage: stage, entries: Array(sortedEntries))
        }
    }

    func dashboardStats(for candidate: PlayerCandidate) throws -> PlayerDashboardStats {
        try ensureReady()
        let playerID = stablePlayerID(for: candidate)
        let archiveRows = try select(
            """
            SELECT COUNT(*), COALESCE(SUM(game_count), 0)
            FROM pgn_archives
            WHERE player_id = ?
            """,
            [playerID]
        )
        let archiveCount = Int(archiveRows.first?.first ?? "") ?? 0
        let cachedGames = Int(archiveRows.first?.dropFirst().first ?? "") ?? 0
        let datedEvents = candidate.events.compactMap(\.endDate)
        let numericRanks = candidate.events.compactMap { Self.numericRank($0.rank) }
        let youthStages = Self.youthStageSummaries(for: candidate)
        let eloPoints = Self.eloChartPoints(for: candidate, stages: youthStages)
        let rankPoints = youthStages.compactMap { stage -> YouthChartPoint? in
            guard let rank = stage.bestRank else { return nil }
            return YouthChartPoint(
                stage: stage.stage,
                value: Double(rank),
                label: "\(rank)",
                subtitle: stage.majorEventName ?? "赛事名次"
            )
        }

        return PlayerDashboardStats(
            eventCount: candidate.events.count,
            cachedPGNArchives: archiveCount,
            cachedGames: cachedGames,
            firstPlaceCount: numericRanks.filter { $0 == 1 }.count,
            topThreeCount: numericRanks.filter { $0 <= 3 }.count,
            birthYear: candidate.birthYear,
            currentStage: Self.currentYouthStage(birthYear: candidate.birthYear),
            youthStages: youthStages,
            eloChartPoints: eloPoints,
            rankChartPoints: rankPoints,
            earliestEventDate: datedEvents.min(),
            latestEventDate: datedEvents.max()
        )
    }

    func fideIDsForQuery(_ query: String) throws -> [String] {
        try ensureReady()
        let normalized = Self.normalizedAlias(query)
        guard !normalized.isEmpty else { return [] }
        let rows = try select(
            """
            SELECT DISTINCT p.fide_id
            FROM players p
            JOIN player_aliases a ON a.player_id = p.id
            WHERE p.fide_id IS NOT NULL
              AND (a.normalized_alias = ? OR a.normalized_alias LIKE ?)
            ORDER BY CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END
            LIMIT 20
            """,
            [normalized, "\(normalized)%", normalized]
        )
        return rows.compactMap { $0.first?.nilIfBlank }
    }

    func upsert(candidates: [PlayerCandidate]) throws {
        try ensureReady()
        try transaction {
            for candidate in candidates {
                try upsert(candidate: candidate)
            }
        }
    }

    func stats() throws -> DatabaseStats {
        try ensureReady()
        let counts = try select(
            """
            SELECT
              (SELECT COUNT(*) FROM players),
              (SELECT COUNT(*) FROM player_aliases),
              (SELECT COUNT(*) FROM events),
              (SELECT COUNT(*) FROM pgn_archives),
              (SELECT COUNT(*) FROM games)
            """
        )
        var stats = DatabaseStats()
        if let row = counts.first, row.count >= 5 {
            stats.players = Int(row[0]) ?? 0
            stats.aliases = Int(row[1]) ?? 0
            stats.events = Int(row[2]) ?? 0
            stats.pgnArchives = Int(row[3]) ?? 0
            stats.games = Int(row[4]) ?? 0
        }
        stats.pgnBytes = archiveByteCount()
        return stats
    }

    func cachedPGN(for event: TournamentEvent, player: PlayerCandidate) throws -> String? {
        try ensureReady()
        let playerID = stablePlayerID(for: player)
        let eventID = stableEventID(for: event)
        let rows = try select(
            """
            SELECT relative_path
            FROM pgn_archives
            WHERE event_id = ? AND player_id = ?
            ORDER BY downloaded_at DESC
            LIMIT 1
            """,
            [eventID, playerID]
        )
        guard let relativePath = rows.first?.first?.nilIfBlank else { return nil }
        let url = archiveRootURL.appendingPathComponent(relativePath)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try String(contentsOf: url, encoding: .utf8)
    }

    func storePGN(_ pgn: String, event: TournamentEvent, player: PlayerCandidate) throws -> URL {
        try ensureReady()
        let playerID = stablePlayerID(for: player)
        let eventID = stableEventID(for: event)
        let sourceDirectory = archiveRootURL
            .appendingPathComponent(event.source.slugified, isDirectory: true)
            .appendingPathComponent("tnr\(event.tournamentID)", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceDirectory, withIntermediateDirectories: true)

        let fileName = "fide-\(player.fideID ?? playerID)-\(event.tournamentID).pgn"
        let fileURL = sourceDirectory.appendingPathComponent(fileName)
        let games = PGNTools.splitGames(pgn)
        guard !games.isEmpty else {
            throw SQLiteStoreError.invalidPGN
        }
        try pgn.write(to: fileURL, atomically: true, encoding: .utf8)

        let relativePath = fileURL.path.replacingOccurrences(of: archiveRootURL.path + "/", with: "")
        let archiveID = "archive-\(Self.sha256Hex(relativePath + pgn))"
        let pgnHash = Self.sha256Hex(pgn)
        let downloadedAt = ISO8601DateFormatter().string(from: Date())

        try transaction {
            try upsert(player: player)
            try upsert(event: event, playerID: playerID)
            try execute(
                """
                INSERT INTO pgn_archives(id, event_id, player_id, source, relative_path, sha256, game_count, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    game_count = excluded.game_count,
                    downloaded_at = excluded.downloaded_at
                """,
                [archiveID, eventID, playerID, event.source, relativePath, pgnHash, "\(games.count)", downloadedAt]
            )
            try replaceGames(games, archiveID: archiveID, eventID: eventID, player: player)
        }

        return fileURL
    }

    func importEventPGN(_ source: DownloadedEventPGN, allowChinaEventNameFallback: Bool) throws -> ManualEventImportReport {
        try ensureReady()
        let rawGames = PGNTools.splitGames(source.pgn)
        let games = PGNTools.cleanedUniqueGames(source.pgn)
        guard !games.isEmpty else {
            throw SQLiteStoreError.invalidPGN
        }

        let identities = try loadPlayerIdentityIndex()
        var gamesByPlayerID: [String: [String]] = [:]
        var candidatesByPlayerID: [String: PlayerCandidate] = [:]
        var unresolved: [String: Int] = [:]
        var warnings: [String] = []

        for game in games {
            let headers = PGNTools.headers(in: game)
            let chinaEvent = isChinaEvent(headers)
            for side in ["White", "Black"] {
                let name = headers[side]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                guard !name.isEmpty else { continue }
                let fideID = firstHeader(headers, keys: ["\(side)FideId", "\(side)FideID", "\(side)FIDEID", "\(side)Fide"])
                let federation = firstHeader(headers, keys: ["\(side)Fed", "\(side)Federation", "\(side)Country"])
                let identity = identity(
                    name: name,
                    fideID: fideID,
                    federation: federation,
                    chinaEvent: chinaEvent && allowChinaEventNameFallback,
                    identities: identities
                )
                guard let identity else {
                    unresolved[name, default: 0] += 1
                    continue
                }
                gamesByPlayerID[identity.id, default: []].append(game)
                candidatesByPlayerID[identity.id] = identity.candidate
            }
        }

        if gamesByPlayerID.isEmpty {
            warnings.append("未识别到可入库棋手。请先在用户名称映射表中补 alias/fide_id 后重试。")
        }

        let firstHeaders = games.first.map(PGNTools.headers(in:)) ?? [:]
        let eventName = firstHeaders["Event"]?.nilIfBlank ?? "手工导入赛事 \(source.tournamentID)"
        let eventDate = parsePGNDate(firstHeaders["EventDate"] ?? firstHeaders["Date"] ?? "")
        let rounds = firstHeaders["EventRounds"] ?? ""
        let participants = ""
        var summaries: [ManualPlayerImportSummary] = []
        var importedGames = 0
        var importedArchives = 0

        for (playerID, playerGames) in gamesByPlayerID.sorted(by: { $0.key < $1.key }) {
            guard let candidate = candidatesByPlayerID[playerID] else { continue }
            let event = TournamentEvent(
                id: "\(source.tournamentID)-\(candidate.fideID ?? playerID)",
                tournamentID: source.tournamentID,
                playerSerial: nil,
                playerName: candidate.displayName,
                fideID: candidate.fideID,
                club: "",
                federation: candidate.federation,
                name: eventName,
                endDate: eventDate,
                rank: "",
                rounds: rounds,
                participants: participants,
                eventURL: source.sourceURL,
                playerURL: nil,
                source: source.sourceName,
                isLikelyTestData: false
            )
            let mergedPGN = playerGames.joined(separator: "\n\n")
            let fileURL = try storePGN(mergedPGN, event: event, player: candidate)
            importedArchives += 1
            importedGames += playerGames.count
            summaries.append(
                ManualPlayerImportSummary(
                    id: playerID,
                    displayName: candidate.displayName,
                    fideID: candidate.fideID,
                    gameCount: playerGames.count,
                    archivePath: fileURL.path
                )
            )
        }

        return ManualEventImportReport(
            sourceURL: source.sourceURL.absoluteString,
            finalURL: source.finalURL.absoluteString,
            tournamentID: source.tournamentID,
            eventName: eventName,
            totalGames: rawGames.count,
            uniqueGames: games.count,
            importedPlayers: summaries.count,
            importedArchives: importedArchives,
            importedGames: importedGames,
            unresolvedNames: unresolved
                .sorted { lhs, rhs in
                    if lhs.value != rhs.value { return lhs.value > rhs.value }
                    return lhs.key.localizedStandardCompare(rhs.key) == .orderedAscending
                }
                .map { "\($0.key) ×\($0.value)" },
            playerSummaries: summaries.sorted { $0.displayName.localizedStandardCompare($1.displayName) == .orderedAscending },
            warnings: warnings
        )
    }

    var userNameMappingLocation: URL {
        databaseURL.deletingLastPathComponent().appendingPathComponent("user-name-mapping.csv")
    }

    func ensureUserNameMappingTemplate() throws -> URL {
        try ensureReady()
        let url = userNameMappingLocation
        guard !FileManager.default.fileExists(atPath: url.path) else {
            return url
        }
        try (Self.userMappingHeader.joined(separator: ",") + "\n").write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func importUserNameMappings(from url: URL) throws -> NameMappingImportReport {
        try ensureReady()
        let text = try String(contentsOf: url, encoding: .utf8)
        let rows = Self.parseCSV(text)
        guard let header = rows.first?.map({ $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }) else {
            return NameMappingImportReport()
        }
        let records = rows.dropFirst()
        var report = NameMappingImportReport(rows: records.count)
        var seenPlayers: Set<String> = []

        try transaction {
            for (offset, values) in records.enumerated() {
                let row = Self.csvRow(header: header, values: values)
                let alias = row["alias"] ?? ""
                let fideID = row["fide_id"] ?? ""
                let displayName = row["display_name"] ?? ""
                let chineseName = row["chinese_name"] ?? ""
                let pinyinName = row["pinyin_name"] ?? ""
                let englishName = row["english_name"] ?? ""
                let federation = (row["federation"]?.nilIfBlank ?? "CHN")
                let birthYear = row["birth_year"] ?? ""
                let standardRating = row["standard_rating"] ?? ""
                let rapidRating = row["rapid_rating"] ?? ""
                let blitzRating = row["blitz_rating"] ?? ""
                let bestDisplayName = displayName.nilIfBlank ?? chineseName.nilIfBlank ?? englishName.nilIfBlank ?? pinyinName.nilIfBlank ?? alias
                guard !alias.isEmpty || !fideID.isEmpty || !bestDisplayName.isEmpty else {
                    report.skippedRows += 1
                    continue
                }

                let playerID: String
                if !fideID.isEmpty {
                    playerID = "fide-\(fideID)"
                } else {
                    let seed = Self.normalizedAlias(bestDisplayName.isEmpty ? alias : bestDisplayName)
                    guard !seed.isEmpty else {
                        report.skippedRows += 1
                        continue
                    }
                    playerID = "local-\(Self.sha256Hex(seed).prefix(16))"
                }

                do {
                    try upsertManualMappedPlayer(
                        playerID: playerID,
                        fideID: fideID,
                        chineseName: chineseName,
                        pinyinName: pinyinName,
                        englishName: englishName.nilIfBlank ?? bestDisplayName,
                        federation: federation,
                        birthYear: birthYear,
                        standardRating: standardRating,
                        rapidRating: rapidRating,
                        blitzRating: blitzRating
                    )
                    for value in [alias, bestDisplayName, chineseName, pinyinName, englishName, fideID] where !value.isEmpty {
                        try insertAlias(value, type: "manual", source: "user-mapping", playerID: playerID)
                        report.importedAliases += 1
                    }
                    seenPlayers.insert(playerID)
                } catch {
                    report.errors.append("第 \(offset + 2) 行：\(error.localizedDescription)")
                }
            }
        }

        report.importedPlayers = seenPlayers.count
        return report
    }

    func saveUserNameMapping(_ draft: UserNameMappingDraft) throws -> NameMappingImportReport {
        try ensureReady()
        let alias = draft.alias.trimmingCharacters(in: .whitespacesAndNewlines)
        let fideID = draft.fideID.trimmingCharacters(in: .whitespacesAndNewlines)
        let displayName = draft.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        let chineseName = draft.chineseName.trimmingCharacters(in: .whitespacesAndNewlines)
        let pinyinName = draft.pinyinName.trimmingCharacters(in: .whitespacesAndNewlines)
        let englishName = draft.englishName.trimmingCharacters(in: .whitespacesAndNewlines)
        let federation = draft.federation.trimmingCharacters(in: .whitespacesAndNewlines).nilIfBlank ?? "CHN"
        let birthYear = draft.birthYear.trimmingCharacters(in: .whitespacesAndNewlines)
        let standardRating = draft.standardRating.trimmingCharacters(in: .whitespacesAndNewlines)
        let rapidRating = draft.rapidRating.trimmingCharacters(in: .whitespacesAndNewlines)
        let blitzRating = draft.blitzRating.trimmingCharacters(in: .whitespacesAndNewlines)
        let bestDisplayName = displayName.nilIfBlank ?? chineseName.nilIfBlank ?? englishName.nilIfBlank ?? pinyinName.nilIfBlank ?? alias
        guard !alias.isEmpty || !fideID.isEmpty || !bestDisplayName.isEmpty else {
            throw SQLiteStoreError.invalidNameMapping
        }

        let playerID: String
        if !fideID.isEmpty {
            playerID = "fide-\(fideID)"
        } else if !draft.playerID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            playerID = draft.playerID.trimmingCharacters(in: .whitespacesAndNewlines)
        } else {
            let seed = Self.normalizedAlias(bestDisplayName)
            guard !seed.isEmpty else { throw SQLiteStoreError.invalidNameMapping }
            playerID = "local-\(Self.sha256Hex(seed).prefix(16))"
        }

        var report = NameMappingImportReport(rows: 1)
        try transaction {
            try upsertManualMappedPlayer(
                playerID: playerID,
                fideID: fideID,
                chineseName: chineseName,
                pinyinName: pinyinName,
                englishName: englishName.nilIfBlank ?? bestDisplayName,
                federation: federation,
                birthYear: birthYear,
                standardRating: standardRating,
                rapidRating: rapidRating,
                blitzRating: blitzRating
            )
            for value in [alias, bestDisplayName, chineseName, pinyinName, englishName, fideID] where !value.isEmpty {
                try insertAlias(value, type: "manual", source: "user-mapping", playerID: playerID)
                report.importedAliases += 1
            }
        }
        report.importedPlayers = 1
        try persistUserNameMappingCSV(draft, bestDisplayName: bestDisplayName)
        return report
    }

    func userNameMappings(limit: Int = 500) throws -> [UserNameMappingRow] {
        try ensureReady()
        let notesByAlias = (try? userMappingCSVNotesByAlias()) ?? [:]
        return try select(
            """
            SELECT a.player_id, a.alias,
                   COALESCE(NULLIF(p.chinese_name,''), NULLIF(p.english_name,''), NULLIF(p.pinyin_name,''), p.id) AS display_name,
                   COALESCE(p.fide_id, ''),
                   COALESCE(p.chinese_name, ''),
                   COALESCE(p.pinyin_name, ''),
                   COALESCE(p.english_name, ''),
                   COALESCE(p.federation, ''),
                   COALESCE(p.birth_year, ''),
                   COALESCE(p.standard_rating, ''),
                   COALESCE(p.rapid_rating, ''),
                   COALESCE(p.blitz_rating, ''),
                   a.source
            FROM player_aliases a
            JOIN players p ON p.id = a.player_id
            WHERE a.source = 'user-mapping'
            ORDER BY a.created_at DESC, a.alias
            LIMIT ?
            """,
            ["\(limit)"]
        )
        .map { row in
            let alias = row[1]
            return UserNameMappingRow(
                id: "\(row[0])-\(Self.normalizedAlias(alias))",
                playerID: row[0],
                alias: alias,
                displayName: row[2],
                fideID: row[3].nilIfBlank,
                chineseName: row[4],
                pinyinName: row[5],
                englishName: row[6],
                federation: row[7],
                birthYear: row[8],
                standardRating: row[9],
                rapidRating: row[10],
                blitzRating: row[11],
                source: row[12],
                note: notesByAlias[Self.normalizedAlias(alias)] ?? ""
            )
        }
    }

    func allNameMappings(limit: Int = 1000) throws -> [UserNameMappingRow] {
        try ensureReady()
        return try aliasMappingRows(whereClause: "", parameters: [], limit: limit)
    }

    func aliasSourceStats() throws -> [AliasSourceStat] {
        try ensureReady()
        return try select(
            """
            SELECT source, COUNT(*)
            FROM player_aliases
            GROUP BY source
            ORDER BY COUNT(*) DESC, source
            """
        )
        .map { row in
            AliasSourceStat(source: row[0], count: Int(row[1]) ?? 0)
        }
    }

    func exportAllNameMappingsCSV() throws -> URL {
        try ensureReady()
        let rows = try aliasMappingRows(whereClause: "", parameters: [], limit: Int.max)
        let url = databaseURL.deletingLastPathComponent().appendingPathComponent("all-name-mappings.csv")
        var csvRows = [[
            "player_id",
            "alias",
            "source",
            "fide_id",
            "display_name",
            "chinese_name",
            "pinyin_name",
            "english_name",
            "federation",
            "birth_year",
            "standard_rating",
            "rapid_rating",
            "blitz_rating"
        ]]
        csvRows.append(contentsOf: rows.map { row in
            [
                row.playerID,
                row.alias,
                row.source,
                row.fideID ?? "",
                row.displayName,
                row.chineseName,
                row.pinyinName,
                row.englishName,
                row.federation,
                row.birthYear,
                row.standardRating,
                row.rapidRating,
                row.blitzRating
            ]
        })
        let csv = csvRows.map(Self.csvLine).joined(separator: "\n") + "\n"
        try csv.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    private func aliasMappingRows(whereClause: String, parameters: [String], limit: Int) throws -> [UserNameMappingRow] {
        let notesByAlias = (try? userMappingCSVNotesByAlias()) ?? [:]
        let sqlLimit = limit == Int.max ? "-1" : "\(limit)"
        return try select(
            """
            SELECT a.player_id, a.alias,
                   COALESCE(NULLIF(p.chinese_name,''), NULLIF(p.english_name,''), NULLIF(p.pinyin_name,''), p.id) AS display_name,
                   COALESCE(p.fide_id, ''),
                   COALESCE(p.chinese_name, ''),
                   COALESCE(p.pinyin_name, ''),
                   COALESCE(p.english_name, ''),
                   COALESCE(p.federation, ''),
                   COALESCE(p.birth_year, ''),
                   COALESCE(p.standard_rating, ''),
                   COALESCE(p.rapid_rating, ''),
                   COALESCE(p.blitz_rating, ''),
                   a.source
            FROM player_aliases a
            JOIN players p ON p.id = a.player_id
            \(whereClause)
            ORDER BY a.source, a.alias
            LIMIT ?
            """,
            parameters + [sqlLimit]
        )
        .map { row in
            aliasMappingRow(from: row, notesByAlias: notesByAlias)
        }
    }

    private func aliasMappingRow(from row: [String], notesByAlias: [String: String]) -> UserNameMappingRow {
        let alias = row[1]
        return UserNameMappingRow(
            id: "\(row[0])-\(Self.normalizedAlias(alias))-\(row[12])",
            playerID: row[0],
            alias: alias,
            displayName: row[2],
            fideID: row[3].nilIfBlank,
            chineseName: row[4],
            pinyinName: row[5],
            englishName: row[6],
            federation: row[7],
            birthYear: row[8],
            standardRating: row[9],
            rapidRating: row[10],
            blitzRating: row[11],
            source: row[12],
            note: notesByAlias[Self.normalizedAlias(alias)] ?? ""
        )
    }

    var databaseLocation: URL {
        databaseURL
    }

    var archiveLocation: URL {
        archiveRootURL
    }

    private func prepareSchema() throws {
        try executeScript("""
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            fide_id TEXT UNIQUE,
            chinese_name TEXT,
            pinyin_name TEXT,
            english_name TEXT,
            federation TEXT,
            birth_year INTEGER,
            standard_rating INTEGER,
            rapid_rating INTEGER,
            blitz_rating INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS player_aliases (
            player_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(player_id, normalized_alias),
            FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_player_alias_lookup ON player_aliases(normalized_alias);
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            end_date TEXT,
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            url TEXT,
            rounds TEXT,
            participants TEXT,
            is_test INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source, source_event_id)
        );
        CREATE TABLE IF NOT EXISTS player_events (
            player_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            player_name TEXT,
            rank TEXT,
            club TEXT,
            federation TEXT,
            source_player_id TEXT,
            PRIMARY KEY(player_id, event_id),
            FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS pgn_archives (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            source TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            game_count INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            archive_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            white_name TEXT,
            black_name TEXT,
            white_player_id TEXT,
            black_player_id TEXT,
            date TEXT,
            round TEXT,
            result TEXT,
            eco TEXT,
            pgn_text TEXT NOT NULL,
            pgn_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY(archive_id) REFERENCES pgn_archives(id) ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        """)
    }

    private func migrateSchema() throws {
        try addColumnIfMissing("birth_year", definition: "INTEGER")
        try addColumnIfMissing("standard_rating", definition: "INTEGER")
        try addColumnIfMissing("rapid_rating", definition: "INTEGER")
        try addColumnIfMissing("blitz_rating", definition: "INTEGER")
    }

    private func addColumnIfMissing(_ column: String, definition: String) throws {
        let rows = try select("PRAGMA table_info(players)")
        let names = Set(rows.compactMap { $0.count > 1 ? $0[1] : nil })
        guard !names.contains(column) else { return }
        try execute("ALTER TABLE players ADD COLUMN \(column) \(definition)")
    }

    private func seedChinesePlayers() throws {
        try transaction {
            for seed in ChinesePlayerSeeds.players + YouthLeaderboardSeeds.players {
                let playerID = "fide-\(seed.fideID)"
                try execute(
                    """
                    INSERT INTO players(id, fide_id, chinese_name, pinyin_name, english_name, federation, birth_year, standard_rating, rapid_rating, blitz_rating)
                    VALUES (?, ?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''))
                    ON CONFLICT(id) DO UPDATE SET
                        fide_id = excluded.fide_id,
                        chinese_name = COALESCE(NULLIF(players.chinese_name, ''), excluded.chinese_name),
                        pinyin_name = COALESCE(NULLIF(players.pinyin_name, ''), excluded.pinyin_name),
                        english_name = COALESCE(NULLIF(players.english_name, ''), excluded.english_name),
                        federation = COALESCE(NULLIF(players.federation, ''), excluded.federation),
                        birth_year = COALESCE(players.birth_year, excluded.birth_year),
                        standard_rating = COALESCE(excluded.standard_rating, players.standard_rating),
                        rapid_rating = COALESCE(excluded.rapid_rating, players.rapid_rating),
                        blitz_rating = COALESCE(excluded.blitz_rating, players.blitz_rating),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    [
                        playerID,
                        seed.fideID,
                        seed.chineseName,
                        seed.pinyinName,
                        seed.englishName,
                        seed.federation,
                        seed.birthYear.map(String.init) ?? "",
                        seed.standardRating.map(String.init) ?? "",
                        seed.rapidRating.map(String.init) ?? "",
                        seed.blitzRating.map(String.init) ?? ""
                    ]
                )
                try insertAlias(seed.chineseName, type: "zh", source: "seed", playerID: playerID)
                try insertAlias(seed.pinyinName, type: "pinyin", source: "seed", playerID: playerID)
                try insertAlias(seed.englishName, type: "fide", source: "seed", playerID: playerID)
                for alias in seed.aliases {
                    try insertAlias(alias, type: "manual", source: "seed", playerID: playerID)
                }
            }

            for event in ChineseEventSeeds.events {
                try seed(event: event)
            }
        }
    }

    private func seed(event: ChineseEventSeed) throws {
        let playerID = "fide-\(event.fideID)"
        let eventID = "\(event.source.slugified)-\(event.tournamentID)"
        let url = "https://chess-results.com/tnr\(event.tournamentID).aspx?lan=1"
        try execute(
            """
            INSERT INTO events(id, name, normalized_name, end_date, source, source_event_id, url, rounds, participants, is_test)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(source, source_event_id) DO UPDATE SET
                name = excluded.name,
                normalized_name = excluded.normalized_name,
                end_date = COALESCE(events.end_date, excluded.end_date),
                url = excluded.url,
                rounds = COALESCE(events.rounds, excluded.rounds),
                participants = COALESCE(events.participants, excluded.participants)
            """,
            [
                eventID,
                event.eventName,
                Self.normalizedAlias(event.eventName),
                event.endDate,
                event.source,
                event.tournamentID,
                url,
                event.rounds,
                event.participants
            ]
        )
        try execute(
            """
            INSERT INTO player_events(player_id, event_id, player_name, rank, club, federation, source_player_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, event_id) DO UPDATE SET
                player_name = COALESCE(player_events.player_name, excluded.player_name),
                rank = COALESCE(player_events.rank, excluded.rank),
                club = COALESCE(player_events.club, excluded.club),
                federation = COALESCE(player_events.federation, excluded.federation),
                source_player_id = COALESCE(player_events.source_player_id, excluded.source_player_id)
            """,
            [playerID, eventID, event.playerName, event.rank, event.club, event.federation, event.playerSerial]
        )
        try insertAlias(event.playerName, type: "pgn", source: "seed", playerID: playerID)
    }

    private func upsert(candidate: PlayerCandidate) throws {
        let playerID = stablePlayerID(for: candidate)
        try upsert(player: candidate)
        for event in candidate.events {
            try upsert(event: event, playerID: playerID)
        }
    }

    private func candidate(for playerID: String, includeLikelyTestEvents: Bool) throws -> PlayerCandidate? {
        let rows = try select(
            """
            SELECT id, fide_id, chinese_name, pinyin_name, english_name, federation,
                   birth_year, standard_rating, rapid_rating, blitz_rating
            FROM players
            WHERE id = ?
            LIMIT 1
            """,
            [playerID]
        )
        guard let row = rows.first else { return nil }
        return try candidate(from: row, includeLikelyTestEvents: includeLikelyTestEvents)
    }

    private func candidate(from row: [String], includeLikelyTestEvents: Bool) throws -> PlayerCandidate {
        let playerID = row[0]
        let aliases = try aliases(for: playerID)
        let events = try events(for: playerID, includeLikelyTestEvents: includeLikelyTestEvents)
        return PlayerCandidate(
            id: playerID,
            displayName: Self.displayName(chinese: row[2], pinyin: row[3], english: row[4]),
            fideID: row[1].nilIfBlank,
            federation: row[5].nilIfBlank ?? "CHN",
            birthYear: Int(row[safe: 6] ?? ""),
            standardRating: Int(row[safe: 7] ?? ""),
            rapidRating: Int(row[safe: 8] ?? ""),
            blitzRating: Int(row[safe: 9] ?? ""),
            clubs: try clubs(for: playerID),
            nameVariants: aliases,
            latestEventDate: events.first?.endDate,
            eventCount: events.count,
            source: "本地库",
            events: events
        )
    }

    private func upsert(player candidate: PlayerCandidate) throws {
        let playerID = stablePlayerID(for: candidate)
        try execute(
            """
            INSERT INTO players(id, fide_id, english_name, federation, birth_year, standard_rating, rapid_rating, blitz_rating)
            VALUES (?, ?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''))
            ON CONFLICT(id) DO UPDATE SET
                fide_id = COALESCE(players.fide_id, excluded.fide_id),
                english_name = COALESCE(excluded.english_name, players.english_name),
                federation = COALESCE(excluded.federation, players.federation),
                birth_year = COALESCE(excluded.birth_year, players.birth_year),
                standard_rating = COALESCE(excluded.standard_rating, players.standard_rating),
                rapid_rating = COALESCE(excluded.rapid_rating, players.rapid_rating),
                blitz_rating = COALESCE(excluded.blitz_rating, players.blitz_rating),
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                playerID,
                candidate.fideID ?? "",
                candidate.displayName,
                candidate.federation,
                candidate.birthYear.map(String.init) ?? "",
                candidate.standardRating.map(String.init) ?? "",
                candidate.rapidRating.map(String.init) ?? "",
                candidate.blitzRating.map(String.init) ?? ""
            ]
        )
        if let fideID = candidate.fideID {
            try insertAlias(fideID, type: "fide_id", source: candidate.source, playerID: playerID)
        }
        try insertAlias(candidate.displayName, type: "pgn", source: candidate.source, playerID: playerID)
        for alias in candidate.nameVariants {
            try insertAlias(alias, type: "pgn", source: candidate.source, playerID: playerID)
        }
    }

    private func upsert(event: TournamentEvent, playerID: String) throws {
        let eventID = stableEventID(for: event)
        try execute(
            """
            INSERT INTO events(id, name, normalized_name, end_date, source, source_event_id, url, rounds, participants, is_test)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_event_id) DO UPDATE SET
                name = excluded.name,
                normalized_name = excluded.normalized_name,
                end_date = excluded.end_date,
                url = excluded.url,
                rounds = excluded.rounds,
                participants = excluded.participants,
                is_test = excluded.is_test
            """,
            [
                eventID,
                event.name,
                Self.normalizedAlias(event.name),
                event.endDate.map(AppFormatters.shortDate.string(from:)) ?? "",
                event.source,
                event.tournamentID,
                event.eventURL.absoluteString,
                event.rounds,
                event.participants,
                event.isLikelyTestData ? "1" : "0"
            ]
        )
        try execute(
            """
            INSERT INTO player_events(player_id, event_id, player_name, rank, club, federation, source_player_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, event_id) DO UPDATE SET
                player_name = excluded.player_name,
                rank = excluded.rank,
                club = excluded.club,
                federation = excluded.federation,
                source_player_id = excluded.source_player_id
            """,
            [playerID, eventID, event.playerName, event.rank, event.club, event.federation, event.playerSerial ?? ""]
        )
        try insertAlias(event.playerName, type: "pgn", source: event.source, playerID: playerID)
    }

    private func replaceGames(_ games: [String], archiveID: String, eventID: String, player: PlayerCandidate) throws {
        try execute("DELETE FROM games WHERE archive_id = ?", [archiveID])
        let playerID = stablePlayerID(for: player)
        let playerNames = Set(([player.displayName] + player.nameVariants).map(Self.normalizedAlias))

        for game in games {
            let headers = PGNTools.headers(in: game)
            let pgnHash = Self.sha256Hex(game)
            let white = headers["White"] ?? ""
            let black = headers["Black"] ?? ""
            let whiteID = playerNames.contains(Self.normalizedAlias(white)) ? playerID : ""
            let blackID = playerNames.contains(Self.normalizedAlias(black)) ? playerID : ""
            let gameID = "game-\(pgnHash)"

            try execute(
                """
                INSERT OR IGNORE INTO games(id, archive_id, event_id, white_name, black_name, white_player_id, black_player_id, date, round, result, eco, pgn_text, pgn_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    gameID,
                    archiveID,
                    eventID,
                    white,
                    black,
                    whiteID,
                    blackID,
                    headers["Date"] ?? "",
                    headers["Round"] ?? "",
                    headers["Result"] ?? "",
                    headers["ECO"] ?? "",
                    game,
                    pgnHash
                ]
            )
        }
    }

    private func events(for playerID: String, includeLikelyTestEvents: Bool) throws -> [TournamentEvent] {
        let testFilter = includeLikelyTestEvents ? "" : "AND e.is_test = 0"
        let rows = try select(
            """
            SELECT e.id, e.source_event_id, pe.source_player_id, pe.player_name, p.fide_id, pe.club,
                   pe.federation, e.name, e.end_date, pe.rank, e.rounds, e.participants, e.url,
                   e.source, e.is_test
            FROM events e
            JOIN player_events pe ON pe.event_id = e.id
            JOIN players p ON p.id = pe.player_id
            WHERE pe.player_id = ? \(testFilter)
            ORDER BY e.end_date DESC
            """,
            [playerID]
        )

        return rows.compactMap { row in
            guard row.count >= 15, let url = URL(string: row[12]) else { return nil }
            let eventURL = url
            let playerURL = URL(string: "\(eventURL.deletingLastPathComponent().absoluteString)tnr\(row[1]).aspx?lan=1&art=9&snr=\(row[2])")
            return TournamentEvent(
                id: "\(row[1])-\(row[4])-\(row[2])",
                tournamentID: row[1],
                playerSerial: row[2].nilIfBlank,
                playerName: row[3],
                fideID: row[4].nilIfBlank,
                club: row[5],
                federation: row[6],
                name: row[7],
                endDate: parseDate(row[8]),
                rank: row[9],
                rounds: row[10],
                participants: row[11],
                eventURL: eventURL,
                playerURL: playerURL,
                source: row[13],
                isLikelyTestData: row[14] == "1"
            )
        }
    }

    private func aliases(for playerID: String) throws -> [String] {
        try select(
            "SELECT alias FROM player_aliases WHERE player_id = ? ORDER BY alias_type, alias",
            [playerID]
        )
        .compactMap(\.first)
        .orderedUnique()
    }

    private func clubs(for playerID: String) throws -> [String] {
        try select(
            "SELECT DISTINCT club FROM player_events WHERE player_id = ? AND club <> '' ORDER BY club LIMIT 4",
            [playerID]
        )
        .compactMap(\.first)
    }

    private func insertAlias(_ alias: String, type: String, source: String, playerID: String) throws {
        let trimmed = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        try execute(
            """
            INSERT OR IGNORE INTO player_aliases(player_id, alias, normalized_alias, alias_type, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            [playerID, trimmed, Self.normalizedAlias(trimmed), type, source]
        )
    }

    private func upsertManualMappedPlayer(
        playerID: String,
        fideID: String,
        chineseName: String,
        pinyinName: String,
        englishName: String,
        federation: String,
        birthYear: String,
        standardRating: String,
        rapidRating: String,
        blitzRating: String
    ) throws {
        try execute(
            """
            INSERT INTO players(id, fide_id, chinese_name, pinyin_name, english_name, federation, birth_year, standard_rating, rapid_rating, blitz_rating)
            VALUES (?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''))
            ON CONFLICT(id) DO UPDATE SET
                fide_id = COALESCE(NULLIF(excluded.fide_id, ''), players.fide_id),
                chinese_name = COALESCE(NULLIF(excluded.chinese_name, ''), players.chinese_name),
                pinyin_name = COALESCE(NULLIF(excluded.pinyin_name, ''), players.pinyin_name),
                english_name = COALESCE(NULLIF(excluded.english_name, ''), players.english_name),
                federation = COALESCE(NULLIF(excluded.federation, ''), players.federation),
                birth_year = COALESCE(excluded.birth_year, players.birth_year),
                standard_rating = COALESCE(excluded.standard_rating, players.standard_rating),
                rapid_rating = COALESCE(excluded.rapid_rating, players.rapid_rating),
                blitz_rating = COALESCE(excluded.blitz_rating, players.blitz_rating),
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                playerID,
                fideID,
                chineseName,
                pinyinName,
                englishName,
                federation,
                birthYear,
                standardRating,
                rapidRating,
                blitzRating
            ]
        )
    }

    private func loadPlayerIdentityIndex() throws -> PlayerIdentityIndex {
        let playerRows = try select(
            """
            SELECT id, COALESCE(fide_id, ''), COALESCE(chinese_name, ''), COALESCE(pinyin_name, ''),
                   COALESCE(english_name, ''), COALESCE(federation, 'CHN'), COALESCE(birth_year, ''),
                   COALESCE(standard_rating, ''), COALESCE(rapid_rating, ''), COALESCE(blitz_rating, '')
            FROM players
            """
        )
        var identitiesByID: [String: StoredPlayerIdentity] = [:]
        for row in playerRows {
            let aliases = [row[1], row[2], row[3], row[4]].filter { !$0.isEmpty }
            let identity = StoredPlayerIdentity(
                id: row[0],
                fideID: row[1].nilIfBlank,
                displayName: Self.displayName(chinese: row[2], pinyin: row[3], english: row[4]),
                federation: row[5].nilIfBlank ?? "CHN",
                birthYear: Int(row[6]),
                standardRating: Int(row[7]),
                rapidRating: Int(row[8]),
                blitzRating: Int(row[9]),
                aliases: aliases
            )
            identitiesByID[identity.id] = identity
        }

        var byFIDEID: [String: String] = [:]
        var aliasOwners: [String: String?] = [:]
        let aliasRows = try select("SELECT player_id, alias FROM player_aliases")
        for row in aliasRows {
            guard row.count >= 2, identitiesByID[row[0]] != nil else { continue }
            let normalized = Self.normalizedAlias(row[1])
            guard !normalized.isEmpty else { continue }
            if let existing = aliasOwners[normalized], existing != row[0] {
                aliasOwners[normalized] = nil
            } else {
                aliasOwners[normalized] = row[0]
            }
        }
        for identity in identitiesByID.values {
            if let fideID = identity.fideID, !fideID.isEmpty {
                byFIDEID[fideID] = identity.id
            }
        }

        return PlayerIdentityIndex(
            identitiesByID: identitiesByID,
            byFIDEID: byFIDEID,
            byNormalizedAlias: aliasOwners.compactMapValues { $0 }
        )
    }

    private func identity(
        name: String,
        fideID: String?,
        federation: String?,
        chinaEvent: Bool,
        identities: PlayerIdentityIndex
    ) -> ImportPlayerIdentity? {
        if
            let fideID = fideID?.nilIfBlank,
            let playerID = identities.byFIDEID[fideID],
            let stored = identities.identitiesByID[playerID]
        {
            return ImportPlayerIdentity(id: playerID, candidate: stored.candidate)
        }

        let normalizedName = Self.normalizedAlias(name)
        if
            let playerID = identities.byNormalizedAlias[normalizedName],
            let stored = identities.identitiesByID[playerID]
        {
            return ImportPlayerIdentity(id: playerID, candidate: stored.candidate)
        }

        let isChinaPlayer = (federation ?? "").uppercased() == "CHN"
        guard isChinaPlayer || chinaEvent else { return nil }

        let playerID: String
        if let fideID = fideID?.nilIfBlank {
            playerID = "fide-\(fideID)"
        } else {
            playerID = "local-\(Self.sha256Hex(normalizedName).prefix(16))"
        }
        let candidate = PlayerCandidate(
            id: playerID,
            displayName: name,
            fideID: fideID?.nilIfBlank,
            federation: isChinaPlayer ? "CHN" : ((federation?.nilIfBlank) ?? "CHN"),
            clubs: [],
            nameVariants: [name],
            latestEventDate: nil,
            eventCount: 0,
            source: "手工入库",
            events: []
        )
        return ImportPlayerIdentity(id: playerID, candidate: candidate)
    }

    private func firstHeader(_ headers: [String: String], keys: [String]) -> String? {
        for key in keys {
            if let value = headers[key]?.nilIfBlank {
                return value
            }
        }
        return nil
    }

    private func isChinaEvent(_ headers: [String: String]) -> Bool {
        let eventCountry = (headers["EventCountry"] ?? "").uppercased()
        if eventCountry == "CHN" {
            return true
        }
        let text = [
            headers["Event"] ?? "",
            headers["Site"] ?? ""
        ]
        .joined(separator: " ")
        .lowercased()
        return text.contains("china")
            || text.contains("chinese")
            || text.contains("李成智")
            || text.contains("全国")
            || text.contains("棋协")
    }

    private func parsePGNDate(_ text: String) -> Date? {
        let normalized = text
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ".", with: "-")
            .replacingOccurrences(of: "??", with: "01")
        return AppFormatters.shortDate.date(from: normalized)
    }

    private func stablePlayerID(for candidate: PlayerCandidate) -> String {
        if let fideID = candidate.fideID, !fideID.isEmpty {
            return "fide-\(fideID)"
        }
        return candidate.id.hasPrefix("local-") || candidate.id.hasPrefix("fide-") ? candidate.id : "local-\(candidate.id)"
    }

    private func stableEventID(for event: TournamentEvent) -> String {
        "\(event.source.slugified)-\(event.tournamentID)"
    }

    private func ensureReady() throws {
        if let initializationError {
            throw initializationError
        }
        guard db != nil else {
            throw SQLiteStoreError.openFailed("Database is not open")
        }
    }

    private func transaction(_ work: () throws -> Void) throws {
        try execute("BEGIN IMMEDIATE;")
        do {
            try work()
            try execute("COMMIT;")
        } catch {
            try? execute("ROLLBACK;")
            throw error
        }
    }

    private func execute(_ sql: String, _ values: [String] = []) throws {
        guard let db else { throw SQLiteStoreError.openFailed("Database is not open") }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else {
            throw SQLiteStoreError.prepareFailed(errorMessage)
        }
        defer { sqlite3_finalize(statement) }

        try bind(values, to: statement)
        while true {
            let result = sqlite3_step(statement)
            if result == SQLITE_DONE || result == SQLITE_ROW {
                if result == SQLITE_DONE { break }
            } else {
                throw SQLiteStoreError.stepFailed(errorMessage)
            }
        }
    }

    private func executeScript(_ sql: String) throws {
        guard let db else { throw SQLiteStoreError.openFailed("Database is not open") }
        var errorPointer: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &errorPointer) != SQLITE_OK {
            let message = errorPointer.map { String(cString: $0) } ?? errorMessage
            sqlite3_free(errorPointer)
            throw SQLiteStoreError.stepFailed(message)
        }
    }

    private func select(_ sql: String, _ values: [String] = []) throws -> [[String]] {
        guard let db else { throw SQLiteStoreError.openFailed("Database is not open") }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else {
            throw SQLiteStoreError.prepareFailed(errorMessage)
        }
        defer { sqlite3_finalize(statement) }

        try bind(values, to: statement)
        var rows: [[String]] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            let count = sqlite3_column_count(statement)
            var row: [String] = []
            for index in 0..<count {
                if let text = sqlite3_column_text(statement, index) {
                    row.append(String(cString: text))
                } else {
                    row.append("")
                }
            }
            rows.append(row)
        }
        return rows
    }

    private func bind(_ values: [String], to statement: OpaquePointer?) throws {
        for (offset, value) in values.enumerated() {
            let result = sqlite3_bind_text(statement, Int32(offset + 1), value, -1, sqliteTransient)
            if result != SQLITE_OK {
                throw SQLiteStoreError.bindFailed(errorMessage)
            }
        }
    }

    private var errorMessage: String {
        guard let db else { return "Database is not open" }
        return String(cString: sqlite3_errmsg(db))
    }

    private func parseDate(_ text: String) -> Date? {
        AppFormatters.shortDate.date(from: text)
    }

    static func normalizedAlias(_ alias: String) -> String {
        alias
            .folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: Locale(identifier: "zh_CN"))
            .lowercased()
            .replacingOccurrences(of: "[\\s,.'·，。\\-_]+", with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func displayName(chinese: String, pinyin: String, english: String) -> String {
        chinese.nilIfBlank ?? english.nilIfBlank ?? pinyin.nilIfBlank ?? "未知棋手"
    }

    private static func numericRank(_ rank: String) -> Int? {
        let trimmed = rank.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let range = trimmed.range(of: #"^\d+"#, options: .regularExpression) else { return nil }
        return Int(trimmed[range])
    }

    private static func currentYouthStage(birthYear: Int?) -> YouthStage? {
        guard let birthYear else { return nil }
        return YouthStageRules.stage(
            forBirthYear: birthYear,
            in: YouthStageRules.currentCompetitionYear
        )
    }

    private static func leaderboardRating(for candidate: PlayerCandidate) -> (value: Int, kind: FIDERatingSnapshot.Kind)? {
        if let standard = candidate.standardRating {
            return (standard, .standard)
        }
        if let rapid = candidate.rapidRating {
            return (rapid, .rapid)
        }
        if let blitz = candidate.blitzRating {
            return (blitz, .blitz)
        }
        return nil
    }

    private static func liChengzhiTopThreeNote(for candidate: PlayerCandidate, stage: YouthStage) -> String? {
        let matches = candidate.events.compactMap { event -> (rank: Int, event: TournamentEvent)? in
            guard
                eventStage(for: event, birthYear: candidate.birthYear) == stage,
                isLiChengzhiCupLike(event.name),
                let rank = numericRank(event.rank),
                rank <= 3
            else { return nil }
            return (rank, event)
        }
        .sorted {
            if $0.rank != $1.rank { return $0.rank < $1.rank }
            return ($0.event.endDate ?? .distantPast) > ($1.event.endDate ?? .distantPast)
        }

        guard let best = matches.first else { return nil }
        return "李成智杯第 \(best.rank)"
    }

    private static func isLiChengzhiCupLike(_ eventName: String) -> Bool {
        let normalized = normalizedAlias(eventName)
        let lowercased = eventName.lowercased()
        return normalized.contains("李成智")
            || lowercased.contains("li chengzhi")
            || lowercased.contains("national youth chess championship")
    }

    private static func youthStageSummaries(for candidate: PlayerCandidate) -> [YouthStageSummary] {
        YouthStage.allCases.map { stage in
            let events = candidate.events.filter {
                eventStage(for: $0, birthYear: candidate.birthYear) == stage
            }
            let rankedEvents = events.compactMap { event -> (rank: Int, event: TournamentEvent)? in
                guard let rank = numericRank(event.rank) else { return nil }
                return (rank, event)
            }
            .sorted {
                if $0.rank != $1.rank { return $0.rank < $1.rank }
                return ($0.event.endDate ?? .distantPast) > ($1.event.endDate ?? .distantPast)
            }

            return YouthStageSummary(
                stage: stage,
                status: status(for: stage, birthYear: candidate.birthYear),
                eventCount: events.count,
                bestRank: rankedEvents.first?.rank,
                majorEventName: rankedEvents.first?.event.name,
                peakRating: peakRating(for: stage, candidate: candidate)
                    ?? (currentYouthStage(birthYear: candidate.birthYear) == stage ? currentBestRating(candidate) : nil)
            )
        }
    }

    private static func eloChartPoints(for candidate: PlayerCandidate, stages: [YouthStageSummary]) -> [YouthChartPoint] {
        var points = stages.compactMap { stage -> YouthChartPoint? in
            guard let rating = stage.peakRating else { return nil }
            return YouthChartPoint(
                stage: stage.stage,
                value: Double(rating),
                label: "\(rating)",
                subtitle: "FIDE 峰值"
            )
        }

        if points.isEmpty, let currentStage = currentYouthStage(birthYear: candidate.birthYear) {
            let ratings = [candidate.standardRating, candidate.rapidRating, candidate.blitzRating].compactMap { $0 }
            if let rating = ratings.max() {
                points.append(
                    YouthChartPoint(
                        stage: currentStage,
                        value: Double(rating),
                        label: "\(rating)",
                        subtitle: "当前 FIDE"
                    )
                )
            }
        }
        return points
    }

    private static func peakRating(for stage: YouthStage, candidate: PlayerCandidate) -> Int? {
        guard let birthYear = candidate.birthYear else { return nil }
        let preferredKind = preferredRatingKind(candidate.fideRatingHistory)
        let ratings = candidate.fideRatingHistory.compactMap { snapshot -> Int? in
            guard preferredKind == nil || snapshot.kind == preferredKind else { return nil }
            guard YouthStageRules.stage(forBirthYear: birthYear, in: snapshot.year) == stage else { return nil }
            return snapshot.rating
        }
        return ratings.max()
    }

    private static func preferredRatingKind(_ snapshots: [FIDERatingSnapshot]) -> FIDERatingSnapshot.Kind? {
        for kind in [FIDERatingSnapshot.Kind.standard, .rapid, .blitz] where snapshots.contains(where: { $0.kind == kind }) {
            return kind
        }
        return nil
    }

    private static func currentBestRating(_ candidate: PlayerCandidate) -> Int? {
        [candidate.standardRating, candidate.rapidRating, candidate.blitzRating].compactMap { $0 }.max()
    }

    private static func eventStage(for event: TournamentEvent, birthYear: Int?) -> YouthStage? {
        if
            let birthYear,
            let date = event.endDate
        {
            let eventYear = Calendar(identifier: .gregorian).component(.year, from: date)
            if let stage = YouthStageRules.stage(forBirthYear: birthYear, in: eventYear) {
                return stage
            }
        }
        return YouthStage.stage(fromEventName: event.name)
    }

    private static func status(for stage: YouthStage, birthYear: Int?) -> YouthStageStatus {
        guard let birthYear else { return .unknown }
        let currentAge = YouthStageRules.currentCompetitionYear - birthYear
        if currentAge > stage.upperAge {
            return .completed
        }
        if currentAge >= stage.lowerAge {
            return .current
        }
        return .upcoming
    }

    private static func sha256Hex(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private static let userMappingHeader = [
        "alias",
        "fide_id",
        "display_name",
        "chinese_name",
        "pinyin_name",
        "english_name",
        "federation",
        "birth_year",
        "standard_rating",
        "rapid_rating",
        "blitz_rating",
        "note"
    ]

    private func persistUserNameMappingCSV(
        _ draft: UserNameMappingDraft,
        bestDisplayName: String
    ) throws {
        let url = try ensureUserNameMappingTemplate()
        let text = try String(contentsOf: url, encoding: .utf8)
        let parsed = Self.parseCSV(text)
        let header = parsed.first?.map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() } ?? Self.userMappingHeader
        let alias = draft.alias.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedAlias = Self.normalizedAlias(alias)
        let fideID = draft.fideID.trimmingCharacters(in: .whitespacesAndNewlines)
        let newRow = userMappingCSVRow(from: draft, bestDisplayName: bestDisplayName)

        var outputRows = [Self.userMappingHeader]
        var replaced = false
        for values in parsed.dropFirst() {
            let row = Self.csvRow(header: header, values: values)
            let sameAlias = !normalizedAlias.isEmpty && Self.normalizedAlias(row["alias"] ?? "") == normalizedAlias
            let sameFideID = !fideID.isEmpty && row["fide_id"] == fideID
            let sameDisplay = normalizedAlias.isEmpty && !bestDisplayName.isEmpty
                && Self.normalizedAlias(row["display_name"] ?? "") == Self.normalizedAlias(bestDisplayName)
            if sameAlias || sameFideID || sameDisplay {
                if !replaced {
                    outputRows.append(newRow)
                    replaced = true
                }
            } else {
                outputRows.append(Self.userMappingHeader.map { row[$0] ?? "" })
            }
        }
        if !replaced {
            outputRows.append(newRow)
        }
        let csv = outputRows.map(Self.csvLine).joined(separator: "\n") + "\n"
        try csv.write(to: url, atomically: true, encoding: .utf8)
    }

    private func userMappingCSVRow(
        from draft: UserNameMappingDraft,
        bestDisplayName: String
    ) -> [String] {
        [
            draft.alias,
            draft.fideID,
            draft.displayName.nilIfBlank ?? bestDisplayName,
            draft.chineseName,
            draft.pinyinName,
            draft.englishName,
            draft.federation.nilIfBlank ?? "CHN",
            draft.birthYear,
            draft.standardRating,
            draft.rapidRating,
            draft.blitzRating,
            draft.note
        ].map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    private func userMappingCSVNotesByAlias() throws -> [String: String] {
        let url = try ensureUserNameMappingTemplate()
        let text = try String(contentsOf: url, encoding: .utf8)
        let rows = Self.parseCSV(text)
        guard let header = rows.first?.map({ $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }) else {
            return [:]
        }
        var notes: [String: String] = [:]
        for values in rows.dropFirst() {
            let row = Self.csvRow(header: header, values: values)
            let aliasKey = Self.normalizedAlias(row["alias"] ?? "")
            guard !aliasKey.isEmpty else { continue }
            notes[aliasKey] = row["note"] ?? ""
        }
        return notes
    }

    private static func csvRow(header: [String], values: [String]) -> [String: String] {
        Dictionary(
            header.enumerated().map { index, key in
                (key, index < values.count ? values[index].trimmingCharacters(in: .whitespacesAndNewlines) : "")
            },
            uniquingKeysWith: { first, _ in first }
        )
    }

    private static func csvLine(_ values: [String]) -> String {
        values.map { value in
            let escaped = value.replacingOccurrences(of: "\"", with: "\"\"")
            if escaped.contains(",") || escaped.contains("\"") || escaped.contains("\n") {
                return "\"\(escaped)\""
            }
            return escaped
        }
        .joined(separator: ",")
    }

    private static func parseCSV(_ text: String) -> [[String]] {
        var rows: [[String]] = []
        var row: [String] = []
        var field = ""
        var inQuotes = false
        var iterator = text.makeIterator()

        while let character = iterator.next() {
            if inQuotes {
                if character == "\"" {
                    if let next = iterator.next() {
                        if next == "\"" {
                            field.append("\"")
                        } else {
                            inQuotes = false
                            if next == "," {
                                row.append(field)
                                field = ""
                            } else if next == "\n" {
                                row.append(field)
                                rows.append(row)
                                row = []
                                field = ""
                            } else if next != "\r" {
                                field.append(next)
                            }
                        }
                    } else {
                        inQuotes = false
                    }
                } else {
                    field.append(character)
                }
                continue
            }

            switch character {
            case "\"":
                inQuotes = true
            case ",":
                row.append(field)
                field = ""
            case "\n":
                row.append(field)
                rows.append(row)
                row = []
                field = ""
            case "\r":
                continue
            default:
                field.append(character)
            }
        }

        if !field.isEmpty || !row.isEmpty {
            row.append(field)
            rows.append(row)
        }
        return rows.filter { row in
            row.contains { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        }
    }

    private static func applicationSupportURL() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let url = base.appendingPathComponent("ChinaChessPlayerPGN", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func archiveByteCount() -> Int {
        guard let enumerator = FileManager.default.enumerator(
            at: archiveRootURL,
            includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]
        ) else {
            return 0
        }

        var total = 0
        for case let url as URL in enumerator {
            guard
                let values = try? url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey]),
                values.isRegularFile == true
            else { continue }
            total += values.fileSize ?? 0
        }
        return total
    }
}

private struct StoredPlayerIdentity {
    let id: String
    let fideID: String?
    let displayName: String
    let federation: String
    let birthYear: Int?
    let standardRating: Int?
    let rapidRating: Int?
    let blitzRating: Int?
    let aliases: [String]

    var candidate: PlayerCandidate {
        PlayerCandidate(
            id: id,
            displayName: displayName,
            fideID: fideID,
            federation: federation,
            birthYear: birthYear,
            standardRating: standardRating,
            rapidRating: rapidRating,
            blitzRating: blitzRating,
            clubs: [],
            nameVariants: aliases,
            latestEventDate: nil,
            eventCount: 0,
            source: "本地库",
            events: []
        )
    }
}

private struct PlayerIdentityIndex {
    let identitiesByID: [String: StoredPlayerIdentity]
    let byFIDEID: [String: String]
    let byNormalizedAlias: [String: String]
}

private struct ImportPlayerIdentity {
    let id: String
    let candidate: PlayerCandidate
}

enum SQLiteStoreError: LocalizedError {
    case openFailed(String)
    case prepareFailed(String)
    case bindFailed(String)
    case stepFailed(String)
    case invalidPGN
    case invalidNameMapping

    var errorDescription: String? {
        switch self {
        case let .openFailed(message):
            "打开本地数据库失败：\(message)"
        case let .prepareFailed(message):
            "准备 SQL 失败：\(message)"
        case let .bindFailed(message):
            "绑定 SQL 参数失败：\(message)"
        case let .stepFailed(message):
            "执行 SQL 失败：\(message)"
        case .invalidPGN:
            "PGN 内容无有效棋局，已拒绝写入本地归档"
        case .invalidNameMapping:
            "映射行至少需要 alias、FIDE ID 或棋手显示名"
        }
    }
}

private let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

private extension String {
    var nilIfBlank: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    var slugified: String {
        lowercased()
            .replacingOccurrences(of: "[^a-z0-9]+", with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }
}

private extension FIDERatingSnapshot.Kind {
    var sortPriority: Int {
        switch self {
        case .standard: 0
        case .rapid: 1
        case .blitz: 2
        }
    }
}

private extension Array where Element: Hashable {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }

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
