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
            SELECT DISTINCT p.id, p.fide_id, p.chinese_name, p.pinyin_name, p.english_name, p.federation
            FROM players p
            JOIN player_aliases a ON a.player_id = p.id
            WHERE a.normalized_alias = ? OR a.normalized_alias LIKE ?
            ORDER BY CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END, p.english_name
            LIMIT 40
            """,
            [normalized, "\(normalized)%", normalized]
        )

        return try rows.map { row in
            let playerID = row[0]
            let aliases = try aliases(for: playerID)
            let events = try events(for: playerID, includeLikelyTestEvents: includeLikelyTestEvents)
            return PlayerCandidate(
                id: playerID,
                displayName: row[4].nilIfBlank ?? row[2].nilIfBlank ?? row[3].nilIfBlank ?? "未知棋手",
                fideID: row[1].nilIfBlank,
                federation: row[5].nilIfBlank ?? "CHN",
                clubs: try clubs(for: playerID),
                nameVariants: aliases,
                latestEventDate: events.first?.endDate,
                eventCount: events.count,
                source: "本地库",
                events: events
            )
        }
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
        try pgn.write(to: fileURL, atomically: true, encoding: .utf8)

        let relativePath = fileURL.path.replacingOccurrences(of: archiveRootURL.path + "/", with: "")
        let archiveID = "archive-\(Self.sha256Hex(relativePath + pgn))"
        let pgnHash = Self.sha256Hex(pgn)
        let downloadedAt = ISO8601DateFormatter().string(from: Date())
        let games = PGNTools.splitGames(pgn)

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

    private func seedChinesePlayers() throws {
        try transaction {
            for seed in ChinesePlayerSeeds.players {
                let playerID = "fide-\(seed.fideID)"
                try execute(
                    """
                    INSERT INTO players(id, fide_id, chinese_name, pinyin_name, english_name, federation)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        fide_id = excluded.fide_id,
                        chinese_name = COALESCE(players.chinese_name, excluded.chinese_name),
                        pinyin_name = COALESCE(players.pinyin_name, excluded.pinyin_name),
                        english_name = COALESCE(players.english_name, excluded.english_name),
                        federation = COALESCE(players.federation, excluded.federation),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    [playerID, seed.fideID, seed.chineseName, seed.pinyinName, seed.englishName, seed.federation]
                )
                try insertAlias(seed.chineseName, type: "zh", source: "seed", playerID: playerID)
                try insertAlias(seed.pinyinName, type: "pinyin", source: "seed", playerID: playerID)
                try insertAlias(seed.englishName, type: "fide", source: "seed", playerID: playerID)
                for alias in seed.aliases {
                    try insertAlias(alias, type: "manual", source: "seed", playerID: playerID)
                }
            }
        }
    }

    private func upsert(candidate: PlayerCandidate) throws {
        let playerID = stablePlayerID(for: candidate)
        try upsert(player: candidate)
        for event in candidate.events {
            try upsert(event: event, playerID: playerID)
        }
    }

    private func upsert(player candidate: PlayerCandidate) throws {
        let playerID = stablePlayerID(for: candidate)
        try execute(
            """
            INSERT INTO players(id, fide_id, english_name, federation)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                fide_id = COALESCE(players.fide_id, excluded.fide_id),
                english_name = COALESCE(excluded.english_name, players.english_name),
                federation = COALESCE(excluded.federation, players.federation),
                updated_at = CURRENT_TIMESTAMP
            """,
            [playerID, candidate.fideID ?? "", candidate.displayName, candidate.federation]
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

    private static func sha256Hex(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
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
}

enum SQLiteStoreError: LocalizedError {
    case openFailed(String)
    case prepareFailed(String)
    case bindFailed(String)
    case stepFailed(String)

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
