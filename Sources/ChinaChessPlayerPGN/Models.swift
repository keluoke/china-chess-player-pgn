import Foundation

struct PlayerCandidate: Identifiable, Hashable {
    let id: String
    var displayName: String
    var fideID: String?
    var federation: String
    var birthYear: Int? = nil
    var standardRating: Int? = nil
    var rapidRating: Int? = nil
    var blitzRating: Int? = nil
    var clubs: [String]
    var nameVariants: [String]
    var latestEventDate: Date?
    var eventCount: Int
    var source: String
    var events: [TournamentEvent]
    var fideRatingHistory: [FIDERatingSnapshot] = []

    var detailLine: String {
        let fide = fideID.map { "FIDE \($0)" } ?? "无 FIDE ID"
        let date = latestEventDate.map { AppFormatters.shortDate.string(from: $0) } ?? "无日期"
        return "\(fide) · \(federation.isEmpty ? "未知协会" : federation) · \(eventCount) 场赛事 · 最近 \(date)"
    }

    var profileURL: URL? {
        guard let fideID, !fideID.isEmpty else { return nil }
        return URL(string: "https://ratings.fide.com/profile/\(fideID)")
    }
}

struct RecommendedYouthPlayer: Identifiable, Hashable {
    let seed: RecommendedYouthSeed
    let candidate: PlayerCandidate
    let dashboard: PlayerDashboardStats

    var id: String {
        candidate.id
    }

    var displayName: String {
        seed.chineseName.isEmpty ? candidate.displayName : seed.chineseName
    }

    var subtitle: String {
        "\(seed.pinyinName) · \(seed.englishName)"
    }
}

struct YouthLeaderboard: Identifiable, Hashable {
    let stage: YouthStage
    let entries: [YouthLeaderboardEntry]

    var id: YouthStage { stage }
}

struct YouthLeaderboardEntry: Identifiable, Hashable {
    let stage: YouthStage
    let rank: Int
    let candidate: PlayerCandidate
    let rating: Int
    let ratingKind: FIDERatingSnapshot.Kind
    let note: String?

    var id: String {
        "\(stage.rawValue)-\(candidate.id)"
    }

    var ratingKindText: String {
        switch ratingKind {
        case .standard: "STD"
        case .rapid: "RAP"
        case .blitz: "BLZ"
        }
    }

    var playerMetaText: String {
        let fide = candidate.fideID.map { "FIDE \($0)" } ?? "无 FIDE ID"
        let year = candidate.birthYear.map { "\($0)" } ?? "出生年待补"
        return "\(fide) · \(year)"
    }
}

struct PlayerDashboardStats: Hashable {
    var eventCount = 0
    var cachedPGNArchives = 0
    var cachedGames = 0
    var bulkYouthGames = 0
    var bulkYouthStages: [BulkPlayerYouthStageSummary] = []
    var firstPlaceCount = 0
    var topThreeCount = 0
    var birthYear: Int?
    var currentStage: YouthStage?
    var youthStages: [YouthStageSummary] = YouthStage.allCases.map {
        YouthStageSummary(stage: $0, status: .unknown)
    }
    var eloChartPoints: [YouthChartPoint] = []
    var rankChartPoints: [YouthChartPoint] = []
    var earliestEventDate: Date?
    var latestEventDate: Date?

    var activeYearsText: String {
        let calendar = Calendar(identifier: .gregorian)
        let firstYear = earliestEventDate.map { calendar.component(.year, from: $0) }
        let latestYear = latestEventDate.map { calendar.component(.year, from: $0) }

        switch (firstYear, latestYear) {
        case let (.some(first), .some(latest)) where first == latest:
            return "\(latest)"
        case let (.some(first), .some(latest)):
            return "\(first)-\(latest)"
        case let (.none, .some(latest)):
            return "\(latest)"
        case let (.some(first), .none):
            return "\(first)"
        case (.none, .none):
            return "-"
        }
    }

    var latestEventText: String {
        latestEventDate.map { AppFormatters.shortDate.string(from: $0) } ?? "-"
    }
}

enum YouthStage: String, CaseIterable, Hashable, Identifiable {
    case u8 = "U8"
    case u10 = "U10"
    case u12 = "U12"
    case u14 = "U14"
    case u16 = "U16"
    case u18 = "U18"

    var id: String { rawValue }

    var lowerAge: Int {
        switch self {
        case .u8: 7
        case .u10: 9
        case .u12: 11
        case .u14: 13
        case .u16: 15
        case .u18: 17
        }
    }

    var upperAge: Int {
        switch self {
        case .u8: 8
        case .u10: 10
        case .u12: 12
        case .u14: 14
        case .u16: 16
        case .u18: 18
        }
    }

    var previousUpperAge: Int {
        lowerAge - 1
    }

    static func stage(forAge age: Int) -> YouthStage? {
        allCases.first { ($0.lowerAge...$0.upperAge).contains(age) }
    }

    static func stage(fromEventName name: String) -> YouthStage? {
        let normalized = name.uppercased()
        for stage in allCases.reversed() where normalized.contains(stage.rawValue) {
            return stage
        }
        return nil
    }

    func birthYearRangeText(in competitionYear: Int) -> String {
        "\(competitionYear - upperAge)-\(competitionYear - lowerAge) 出生"
    }

    var ageBandText: String {
        "\(lowerAge)-\(upperAge) 岁"
    }
}

enum YouthStageRules {
    static var currentCompetitionYear: Int {
        competitionYear()
    }

    static func competitionYear(for date: Date = Date()) -> Int {
        Calendar(identifier: .gregorian).component(.year, from: date)
    }

    static func stage(forBirthYear birthYear: Int, in competitionYear: Int) -> YouthStage? {
        YouthStage.stage(forAge: competitionYear - birthYear)
    }

    static var currentDefinitionText: String {
        definitionText(for: currentCompetitionYear)
    }

    static var currentCompactDefinitionText: String {
        compactDefinitionText(for: currentCompetitionYear)
    }

    static func definitionText(for competitionYear: Int) -> String {
        let ranges = YouthStage.allCases
            .map { "\($0.rawValue) \($0.birthYearRangeText(in: competitionYear))" }
            .joined(separator: " · ")
        return "按李成智杯自然年龄组：以比赛年度 - 出生年份计算，两年一组；\(competitionYear) 年口径为 \(ranges)。"
    }

    static func compactDefinitionText(for competitionYear: Int) -> String {
        "李成智杯口径：按比赛年度 \(competitionYear) - 出生年份划组"
    }
}

enum YouthStageStatus: Hashable {
    case completed
    case current
    case upcoming
    case unknown

    var label: String {
        switch self {
        case .completed: "已完成"
        case .current: "进行中"
        case .upcoming: "未完待续"
        case .unknown: "待补"
        }
    }
}

struct YouthStageSummary: Identifiable, Hashable {
    let stage: YouthStage
    var status: YouthStageStatus
    var eventCount = 0
    var bestRank: Int?
    var majorEventName: String?
    var peakRating: Int?

    var id: YouthStage { stage }

    var rankText: String {
        bestRank.map { "第 \($0)" } ?? "-"
    }

    var ratingText: String {
        peakRating.map(String.init) ?? "-"
    }
}

struct YouthChartPoint: Identifiable, Hashable {
    let stage: YouthStage
    let value: Double
    let label: String
    let subtitle: String

    var id: YouthStage { stage }
}

struct FIDERatingSnapshot: Hashable {
    enum Kind: String, Hashable {
        case standard
        case rapid
        case blitz

        var label: String {
            switch self {
            case .standard: "Classical"
            case .rapid: "Rapid"
            case .blitz: "Blitz"
            }
        }
    }

    let kind: Kind
    let year: Int
    let month: Int
    let rating: Int
}

struct TournamentEvent: Identifiable, Hashable {
    let id: String
    let tournamentID: String
    let playerSerial: String?
    let playerName: String
    let fideID: String?
    let club: String
    let federation: String
    let name: String
    let endDate: Date?
    let rank: String
    let rounds: String
    let participants: String
    let eventURL: URL
    let playerURL: URL?
    let source: String
    let isLikelyTestData: Bool

    var dateText: String {
        endDate.map { AppFormatters.shortDate.string(from: $0) } ?? "未知"
    }

    var compactMeta: String {
        let rd = rounds.isEmpty ? "?" : rounds
        let players = participants.isEmpty ? "?" : participants
        return "\(rd) 轮 · \(players) 人"
    }
}

struct PGNDownloadResult: Identifiable, Hashable {
    let id = UUID()
    let event: TournamentEvent
    let status: PGNDownloadStatus
    let pgn: String

    var gameCount: Int {
        PGNTools.gameCount(in: pgn)
    }

    var byteCount: Int {
        pgn.data(using: .utf8)?.count ?? 0
    }
}

struct DatabaseStats: Hashable {
    var players = 0
    var aliases = 0
    var events = 0
    var pgnArchives = 0
    var games = 0
    var pgnBytes = 0

    var pgnSizeText: String {
        ByteCountFormatter.string(fromByteCount: Int64(pgnBytes), countStyle: .file)
    }
}

struct BulkDataStats: Hashable {
    var isAvailable = false
    var source = "未加载"
    var license = ""
    var rootURL: URL?
    var mirroredGames = 0
    var mirroredShards = 0
    var mirroredBytes = 0
    var youthGames = 0
    var youthPlayers = 0
    var youthStages: [BulkYouthStagePack] = []
    var byPlayerPlayers = 0
    var byPlayerGames = 0
    var byPlayerPackages = 0

    var mirroredSizeText: String {
        ByteCountFormatter.string(fromByteCount: Int64(mirroredBytes), countStyle: .file)
    }

    var locationText: String {
        rootURL?.path ?? "未找到 docs/data"
    }
}

struct BulkYouthStagePack: Identifiable, Hashable {
    let id: String
    let lowerAge: Int
    let upperAge: Int
    let birthYears: String
    let games: Int
    let players: Int
    let pgnPath: String
    let indexPath: String

    var ageBandText: String {
        "\(lowerAge)-\(upperAge) 岁"
    }
}

struct BulkPlayerYouthStageSummary: Identifiable, Hashable {
    let stageID: String
    let games: Int
    let pgnPath: String
    let indexPath: String

    var id: String { stageID }
}

enum AppPage: String, Hashable {
    case home
    case player
    case manualImport
}

struct DownloadedEventPGN: Hashable {
    let sourceURL: URL
    let finalURL: URL
    let tournamentID: String
    let sourceName: String
    let pgn: String
}

struct ManualEventImportReport: Hashable {
    var sourceURL = ""
    var finalURL = ""
    var tournamentID = ""
    var eventName = ""
    var totalGames = 0
    var uniqueGames = 0
    var importedPlayers = 0
    var importedArchives = 0
    var importedGames = 0
    var unresolvedNames: [String] = []
    var playerSummaries: [ManualPlayerImportSummary] = []
    var warnings: [String] = []
}

struct ManualPlayerImportSummary: Identifiable, Hashable {
    let id: String
    let displayName: String
    let fideID: String?
    let gameCount: Int
    let archivePath: String
}

struct NameMappingImportReport: Hashable {
    var rows = 0
    var importedPlayers = 0
    var importedAliases = 0
    var skippedRows = 0
    var errors: [String] = []
}

struct GitHubPublishResult: Hashable {
    var repoPath = ""
    var copied = 0
    var downloaded = 0
    var skipped = 0
    var pgnFiles = 0
    var games = 0
    var committed = false
    var pushed = false
    var commitHash: String?
    var message = ""
    var warnings: [String] = []

    var hasStatus: Bool {
        !repoPath.isEmpty || !message.isEmpty || !warnings.isEmpty
    }
}

struct UserNameMappingRow: Identifiable, Hashable {
    let id: String
    let playerID: String
    let alias: String
    let displayName: String
    let fideID: String?
    let chineseName: String
    let pinyinName: String
    let englishName: String
    let federation: String
    let birthYear: String
    let standardRating: String
    let rapidRating: String
    let blitzRating: String
    let source: String
    let note: String
}

struct AliasSourceStat: Identifiable, Hashable {
    let source: String
    let count: Int

    var id: String { source }
}

struct UserNameMappingDraft: Hashable {
    var playerID = ""
    var alias = ""
    var fideID = ""
    var displayName = ""
    var chineseName = ""
    var pinyinName = ""
    var englishName = ""
    var federation = "CHN"
    var birthYear = ""
    var standardRating = ""
    var rapidRating = ""
    var blitzRating = ""
    var note = ""

    init() {}

    init(row: UserNameMappingRow) {
        playerID = row.playerID
        alias = row.alias
        fideID = row.fideID ?? ""
        displayName = row.displayName
        chineseName = row.chineseName
        pinyinName = row.pinyinName
        englishName = row.englishName
        federation = row.federation.isEmpty ? "CHN" : row.federation
        birthYear = row.birthYear
        standardRating = row.standardRating
        rapidRating = row.rapidRating
        blitzRating = row.blitzRating
        note = row.note
    }
}

enum PGNDownloadStatus: Hashable {
    case cached
    case success
    case empty
    case failed(String)

    var label: String {
        switch self {
        case .cached:
            "本地缓存"
        case .success:
            "已下载"
        case .empty:
            "无棋谱"
        case .failed:
            "失败"
        }
    }

    var symbolName: String {
        switch self {
        case .cached:
            "tray.full.fill"
        case .success:
            "checkmark.circle.fill"
        case .empty:
            "minus.circle"
        case .failed:
            "exclamationmark.triangle.fill"
        }
    }
}

enum AppFormatters {
    static let shortDate: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static let pgnStamp: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmm"
        return formatter
    }()
}
