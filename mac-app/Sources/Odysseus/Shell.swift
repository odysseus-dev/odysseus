import Foundation

/// Subprocess runner.
///
/// GUI apps on macOS launch with a sparse PATH (`/usr/bin:/bin:/usr/sbin:/sbin`),
/// so commands installed by Homebrew or Docker Desktop are invisible unless we
/// extend PATH ourselves. We prepend the standard locations on every call.
enum Shell {
    struct Result {
        let exitCode: Int32
        let stdout: String
        let stderr: String
        var ok: Bool { exitCode == 0 }
    }

    static let extraPaths = [
        "/opt/homebrew/bin",       // Apple Silicon Homebrew
        "/usr/local/bin",          // Intel Homebrew + Docker Desktop CLI
        "/Applications/Docker.app/Contents/Resources/bin",
    ]

    /// Run a command and capture stdout/stderr. Returns nil if the executable can't be located.
    @discardableResult
    static func run(_ command: String, _ args: [String] = [], env: [String: String]? = nil, timeout: TimeInterval = 60) -> Result {
        guard let path = which(command) else {
            return Result(exitCode: 127, stdout: "", stderr: "command not found: \(command)")
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: path)
        task.arguments = args

        var environment = ProcessInfo.processInfo.environment
        let currentPath = environment["PATH"] ?? ""
        environment["PATH"] = (extraPaths + [currentPath]).filter { !$0.isEmpty }.joined(separator: ":")
        if let env { for (k, v) in env { environment[k] = v } }
        task.environment = environment

        let outPipe = Pipe()
        let errPipe = Pipe()
        task.standardOutput = outPipe
        task.standardError = errPipe

        do { try task.run() } catch {
            return Result(exitCode: 126, stdout: "", stderr: "failed to launch \(command): \(error)")
        }

        // Drain pipes on background queues so a chatty process can't deadlock by filling the kernel buffer.
        var outData = Data(); var errData = Data()
        let group = DispatchGroup()
        group.enter(); DispatchQueue.global().async { outData = outPipe.fileHandleForReading.readDataToEndOfFile(); group.leave() }
        group.enter(); DispatchQueue.global().async { errData = errPipe.fileHandleForReading.readDataToEndOfFile(); group.leave() }

        let deadline = DispatchTime.now() + timeout
        if group.wait(timeout: deadline) == .timedOut {
            task.terminate()
            _ = group.wait(timeout: .now() + 2)
            return Result(exitCode: -1, stdout: String(decoding: outData, as: UTF8.self), stderr: "timed out after \(timeout)s")
        }
        task.waitUntilExit()
        return Result(
            exitCode: task.terminationStatus,
            stdout: String(decoding: outData, as: UTF8.self),
            stderr: String(decoding: errData, as: UTF8.self)
        )
    }

    /// Resolve a bare command name to an absolute path, searching PATH plus our extras.
    /// Absolute paths are returned as-is if they exist.
    static func which(_ command: String) -> String? {
        if command.hasPrefix("/") {
            return FileManager.default.isExecutableFile(atPath: command) ? command : nil
        }
        let searchDirs = extraPaths + (ProcessInfo.processInfo.environment["PATH"]?.split(separator: ":").map(String.init) ?? [])
        for dir in searchDirs {
            let candidate = "\(dir)/\(command)"
            if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
        }
        return nil
    }
}
