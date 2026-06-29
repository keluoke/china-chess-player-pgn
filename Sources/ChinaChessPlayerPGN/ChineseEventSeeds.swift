import Foundation

struct ChineseEventSeed {
    let fideID: String
    let playerName: String
    let tournamentID: String
    let playerSerial: String
    let eventName: String
    let endDate: String
    let rounds: String
    let participants: String
    let source: String
    let rank: String
    let club: String
    let federation: String
}

enum ChineseEventSeeds {
    static let events: [ChineseEventSeed] = [
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "1328805", playerSerial: "8", eventName: "2025 Chinese Chess League Division A", endDate: "2026-01-13", rounds: "6", participants: "34", source: "Chess-Results", rank: "-", club: "Shenzhen Pengcheng", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "1313317", playerSerial: "2", eventName: "2025 Shenzhen Nanshan Chess Masters", endDate: "2025-12-15", rounds: "16", participants: "12", source: "Chess-Results", rank: "2", club: "", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "1263711", playerSerial: "32", eventName: "2025 Chinese Chess League Division A", endDate: "2025-10-05", rounds: "11", participants: "101", source: "Chess-Results", rank: "-", club: "Shenzhen Pengcheng", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "1237809", playerSerial: "3", eventName: "2025 The Third China Chess King Tournament", endDate: "2025-08-24", rounds: "8", participants: "12", source: "Chess-Results", rank: "12", club: "", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "1216270", playerSerial: "4", eventName: "4th Chinese Chess Rapid Tournament", endDate: "2025-07-15", rounds: "10", participants: "24", source: "Chess-Results", rank: "4", club: "", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "1072025", playerSerial: "1", eventName: "2024 Chinese Chess League Division A Final", endDate: "2024-12-12", rounds: "10", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "962456", playerSerial: "1", eventName: "2024 Chinese Chess League Division A Regular", endDate: "2024-07-08", rounds: "11", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "857849", playerSerial: "43", eventName: "2023 Chinese Chess League Division A Final", endDate: "2023-12-12", rounds: "10", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8602883", playerName: "Wang, Hao", tournamentID: "776821", playerSerial: "43", eventName: "2023 Chinese Chess League Division A Regular", endDate: "2023-06-17", rounds: "11", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8601429", playerName: "Wang, Yue", tournamentID: "935824", playerSerial: "6", eventName: "2024 China chess championship (Men)", endDate: "2024-05-16", rounds: "11", participants: "12", source: "Chess-Results", rank: "6", club: "", federation: "CHN"),
        .init(fideID: "8608288", playerName: "Xu, Xiangyu", tournamentID: "935824", playerSerial: "4", eventName: "2024 China chess championship (Men)", endDate: "2024-05-16", rounds: "11", participants: "12", source: "Chess-Results", rank: "4", club: "", federation: "CHN"),
        .init(fideID: "8603332", playerName: "Lu, Shanglei", tournamentID: "935824", playerSerial: "2", eventName: "2024 China chess championship (Men)", endDate: "2024-05-16", rounds: "11", participants: "12", source: "Chess-Results", rank: "2", club: "", federation: "CHN"),
        .init(fideID: "8603847", playerName: "Zeng, Chongsheng", tournamentID: "935824", playerSerial: "5", eventName: "2024 China chess championship (Men)", endDate: "2024-05-16", rounds: "11", participants: "12", source: "Chess-Results", rank: "5", club: "", federation: "CHN"),
        .init(fideID: "8602522", playerName: "Zhao, Jun", tournamentID: "935824", playerSerial: "9", eventName: "2024 China chess championship (Men)", endDate: "2024-05-16", rounds: "11", participants: "12", source: "Chess-Results", rank: "9", club: "", federation: "CHN"),
        .init(fideID: "8604940", playerName: "Xu, Yinglun", tournamentID: "935824", playerSerial: "12", eventName: "2024 China chess championship (Men)", endDate: "2024-05-16", rounds: "11", participants: "12", source: "Chess-Results", rank: "12", club: "", federation: "CHN"),
        .init(fideID: "8603677", playerName: "Wei, Yi", tournamentID: "962457", playerSerial: "1", eventName: "2024 Chinese Chess League Division A(Regular)", endDate: "2024-07-08", rounds: "11", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8603405", playerName: "Yu, Yangyi", tournamentID: "962457", playerSerial: "2", eventName: "2024 Chinese Chess League Division A(Regular)", endDate: "2024-07-08", rounds: "11", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8603006", playerName: "Ding, Liren", tournamentID: "918851", playerSerial: "3", eventName: "FIDE Candidates Tournament 2024", endDate: "2024-04-21", rounds: "14", participants: "8", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8603820", playerName: "Hou, Yifan", tournamentID: "972809", playerSerial: "1", eventName: "FIDE Women's Grand Prix 2024/25 - First Leg, Tbilisi", endDate: "2024-08-24", rounds: "9", participants: "10", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8602980", playerName: "Ju, Wenjun", tournamentID: "918852", playerSerial: "1", eventName: "FIDE Women's Candidates Tournament 2024", endDate: "2024-04-21", rounds: "14", participants: "8", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8603642", playerName: "Lei, Tingjie", tournamentID: "918852", playerSerial: "2", eventName: "FIDE Women's Candidates Tournament 2024", endDate: "2024-04-21", rounds: "14", participants: "8", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8601950", playerName: "Bu, Xiangzhi", tournamentID: "962457", playerSerial: "5", eventName: "2024 Chinese Chess League Division A(Regular)", endDate: "2024-07-08", rounds: "11", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN"),
        .init(fideID: "8603162", playerName: "Ni, Hua", tournamentID: "962457", playerSerial: "6", eventName: "2024 Chinese Chess League Division A(Regular)", endDate: "2024-07-08", rounds: "11", participants: "84", source: "Chess-Results", rank: "-", club: "", federation: "CHN")
    ]
}
