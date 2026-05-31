import Foundation
import Combine

class ServerManager: ObservableObject {
    static let shared = ServerManager()
    
    @Published var isRunning = false
    @Published var isStarting = false
    @Published var logs = ""
    @Published var error: String? = nil
    
    private var process: Process?
    private var outputPipe: Pipe?
    private var errorPipe: Pipe?
    private var pingTimer: Timer?
    
    private let defaults = UserDefaults.standard
    
    // Preferences keys
    static let projectPathKey = "OdysseusProjectPath"
    static let pythonPathKey = "OdysseusPythonPath"
    static let portKey = "OdysseusPort"
    static let autostartKey = "OdysseusAutostart"
    
    var projectPath: String {
        get { defaults.string(forKey: Self.projectPathKey) ?? NSHomeDirectory() + "/Downloads/DumbSlut" }
        set { defaults.set(newValue, forKey: Self.projectPathKey) }
    }
    
    var pythonPath: String {
        get { defaults.string(forKey: Self.pythonPathKey) ?? NSHomeDirectory() + "/Downloads/DumbSlut/venv/bin/python" }
        set { defaults.set(newValue, forKey: Self.pythonPathKey) }
    }
    
    var port: Int {
        get { defaults.integer(forKey: Self.portKey) == 0 ? 7000 : defaults.integer(forKey: Self.portKey) }
        set { defaults.set(newValue, forKey: Self.portKey) }
    }
    
    var autostart: Bool {
        get { defaults.object(forKey: Self.autostartKey) == nil ? true : defaults.bool(forKey: Self.autostartKey) }
        set { defaults.set(newValue, forKey: Self.autostartKey) }
    }
    
    init() {
        // Automatically check health on a timer if we should be running
        pingTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            self?.checkHealth()
        }
    }
    
    deinit {
        stopServer()
    }
    
    func startServer() {
        guard !isRunning && !isStarting else { return }
        
        isStarting = true
        self.error = nil
        self.logs = "--- Starting Odysseus Server ---\n"
        
        let path = pythonPath
        let cwd = projectPath
        let serverPort = port
        
        guard FileManager.default.fileExists(atPath: path) else {
            self.error = "Python executable not found at: \(path)"
            self.isStarting = false
            self.logs += "Error: Python executable not found at: \(path)\n"
            return
        }
        
        guard FileManager.default.directoryExists(atPath: cwd) else {
            self.error = "Project directory not found at: \(cwd)"
            self.isStarting = false
            self.logs += "Error: Project directory not found at: \(cwd)\n"
            return
        }
        
        let newProcess = Process()
        newProcess.executableURL = URL(fileURLWithPath: path)
        newProcess.currentDirectoryURL = URL(fileURLWithPath: cwd)
        
        // Run uvicorn app:app --host 127.0.0.1 --port <port>
        newProcess.arguments = ["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "\(serverPort)"]
        
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        newProcess.environment = env
        
        let outPipe = Pipe()
        let errPipe = Pipe()
        newProcess.standardOutput = outPipe
        newProcess.standardError = errPipe
        
        self.process = newProcess
        self.outputPipe = outPipe
        self.errorPipe = errPipe
        
        setupPipeReader(outPipe)
        setupPipeReader(errPipe)
        
        newProcess.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                self?.isRunning = false
                self?.isStarting = false
                self?.logs += "\n--- Server terminated with exit code \(proc.terminationStatus) ---\n"
                self?.process = nil
            }
        }
        
        do {
            try newProcess.run()
            self.logs += "Server process started. Waiting for health check...\n"
            // Wait a moment and verify health
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                self.checkHealth()
            }
        } catch {
            self.error = "Failed to start process: \(error.localizedDescription)"
            self.isRunning = false
            self.isStarting = false
            self.logs += "Error: \(error.localizedDescription)\n"
            self.process = nil
        }
    }
    
    func stopServer() {
        guard let proc = process, proc.isRunning else { return }
        
        self.logs += "\n--- Stopping Server ---\n"
        proc.terminate()
        proc.waitUntilExit()
        self.process = nil
        self.isRunning = false
        self.isStarting = false
    }
    
    private func setupPipeReader(_ pipe: Pipe) {
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            if let str = String(data: data, encoding: .utf8) {
                DispatchQueue.main.async {
                    self?.logs += str
                    // Keep logs at reasonable length (last 100,000 characters)
                    if let logCount = self?.logs.count, logCount > 100_000 {
                        self?.logs = String((self?.logs.suffix(80_000))!)
                    }
                }
            }
        }
    }
    
    func checkHealth() {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/health") else { return }
        
        var request = URLRequest(url: url)
        request.timeoutInterval = 2.0
        
        let task = URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                    if !self.isRunning {
                        self.isRunning = true
                        self.isStarting = false
                        self.logs += "Server health check passed (OK 200).\n"
                    }
                } else {
                    if self.isRunning {
                        self.isRunning = false
                        self.logs += "Server health check failed. Connection lost.\n"
                    }
                }
            }
        }
        task.resume()
    }
}

extension FileManager {
    func directoryExists(atPath path: String) -> Bool {
        var isDir: ObjCBool = false
        let exists = fileExists(atPath: path, isDirectory: &isDir)
        return exists && isDir.boolValue
    }
}
