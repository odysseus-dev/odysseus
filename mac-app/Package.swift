// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Odysseus",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Odysseus",
            path: "Sources/Odysseus",
            exclude: ["Resources"]
        )
    ]
)
