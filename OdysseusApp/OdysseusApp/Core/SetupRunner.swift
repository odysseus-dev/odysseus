import Foundation

enum SetupRunner {
    struct Result {
        let success: Bool
    }

    static func run(repoURL: URL, pythonPath: String, username: String = "", password: String = "") async -> Result {
        return await withCheckedContinuation { continuation in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: pythonPath)
            p.arguments = ["setup.py"]
            p.currentDirectoryURL = repoURL

            var env = ProcessInfo.processInfo.environment
            env["PYTHONUNBUFFERED"] = "1"
            env["ODYSSEUS_SKIP_RUN_HINT"] = "1"
            if !username.isEmpty { env["ODYSSEUS_ADMIN_USER"] = username }
            if !password.isEmpty { env["ODYSSEUS_ADMIN_PASSWORD"] = password }
            p.environment = env

            let outputPipe = Pipe()
            p.standardOutput = outputPipe
            p.standardError = outputPipe

            var outputData = Data()
            outputPipe.fileHandleForReading.readabilityHandler = { fh in
                outputData.append(fh.availableData)
            }

            p.terminationHandler = { proc in
                outputPipe.fileHandleForReading.readabilityHandler = nil
                if let tail = try? outputPipe.fileHandleForReading.readToEnd() {
                    outputData.append(tail)
                }
                continuation.resume(returning: Result(success: proc.terminationStatus == 0))
            }

            do {
                try p.run()
            } catch {
                continuation.resume(returning: Result(success: false))
            }
        }
    }
}
