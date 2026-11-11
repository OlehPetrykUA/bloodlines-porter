// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "BloodlinesKit",
    platforms: [.macOS("26.0")],
    targets: [
        .target(name: "KitApp", path: "Sources/KitApp"),
        .executableTarget(name: "BloodlinesKit", dependencies: ["KitApp"], path: "Sources/BloodlinesKit"),
        .testTarget(name: "KitAppTests", dependencies: ["KitApp"], path: "Tests/KitAppTests"),
    ]
)
