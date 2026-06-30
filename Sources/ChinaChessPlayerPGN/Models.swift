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

struct PlayerDashboardStats: Hashable {
    var eventCount = 0
    var cachedPGNArchives = 0
    var cachedGames = 0
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
        switch self {
        case .u8: 0
        case .u10: 8
        case .u12: 10
        case .u14: 12
        case .u16: 14
        case .u18: 16
        }
    }

    static func stage(forAge age: Int) -> YouthStage? {
        allCases.first { age <= $0.upperAge && age > $0.previousUpperAge }
    }

    static func stage(fromEventName name: String) -> YouthStage? {
        let normalized = name.uppercased()
        for stage in allCases.reversed() where normalized.contains(stage.rawValue) {
            return stage
        }
        return nil
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
