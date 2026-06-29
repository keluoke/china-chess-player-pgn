// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "ChinaChessPlayerPGN",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "ChinaChessPlayerPGN", targets: ["ChinaChessPlayerPGN"])
    ],
    targets: [
        .executableTarget(
            name: "ChinaChessPlayerPGN",
            path: "Sources/ChinaChessPlayerPGN",
            linkerSettings: [
                .linkedLibrary("sqlite3")
            ]
        )
    ]
)
