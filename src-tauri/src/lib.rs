use serde::Serialize;
use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager};

#[derive(Serialize)]
struct AppStatus {
    profile_id: String,
    profile_dir: String,
    backend_ready: bool,
}

struct AppState {
    profile_id: String,
    profile_dir: PathBuf,
    backend: Mutex<BackendClient>,
}

impl Drop for AppState {
    fn drop(&mut self) {
        if let Ok(mut backend) = self.backend.lock() {
            let _ = backend.shutdown();
        }
    }
}

struct BackendClient {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<std::process::ChildStdout>,
    next_id: u64,
    is_shutdown: bool,
}

struct BackendCallError {
    message: String,
    restartable: bool,
}

impl BackendCallError {
    fn restartable(message: String) -> Self {
        Self {
            message,
            restartable: true,
        }
    }

    fn final_error(message: String) -> Self {
        Self {
            message,
            restartable: false,
        }
    }
}

impl BackendClient {
    fn spawn(app: &AppHandle, profile_dir: &Path) -> Result<Self, String> {
        let python = locate_python(app)?;
        let script = locate_sidecar_script(app)?;

        let mut child = Command::new(&python)
            .arg("-u")
            .arg(&script)
            .env("ODYSSEUS_PROFILE_DIR", profile_dir)
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|err| format!("failed to start Python sidecar with {:?}: {}", python, err))?;

        if let Some(stderr) = child.stderr.take() {
            thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for line in reader.lines().flatten() {
                    eprintln!("[python-sidecar] {line}");
                }
            });
        }

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Python sidecar stdin was not available".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Python sidecar stdout was not available".to_string())?;

        let mut client = Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            next_id: 1,
            is_shutdown: false,
        };

        client
            .call_once("health.ping", json!({}))
            .map_err(|err| err.message)?;
        Ok(client)
    }

    fn call_with_recovery(
        &mut self,
        app: &AppHandle,
        profile_dir: &Path,
        method: &str,
        params: Value,
    ) -> Result<Value, String> {
        match self.call_once(method, params.clone()) {
            Ok(value) => Ok(value),
            Err(err) if err.restartable && method != "app.shutdown" => {
                append_shell_log(
                    profile_dir,
                    &format!(
                        "restarting Python sidecar after RPC failure: {}",
                        err.message
                    ),
                );
                self.restart(app, profile_dir, &err.message)?;
                self.call_once(method, params)
                    .map_err(|retry_err| retry_err.message)
            }
            Err(err) => Err(err.message),
        }
    }

    fn call_once(&mut self, method: &str, params: Value) -> Result<Value, BackendCallError> {
        if self.is_shutdown {
            return Err(BackendCallError::restartable(
                "Python sidecar is not running".to_string(),
            ));
        }

        match self.child.try_wait() {
            Ok(Some(status)) => {
                self.is_shutdown = true;
                return Err(BackendCallError::restartable(format!(
                    "Python sidecar exited before handling {method} (status: {status})"
                )));
            }
            Ok(None) => {}
            Err(err) => {
                return Err(BackendCallError::final_error(format!(
                    "failed to inspect Python sidecar before {method}: {err}"
                )));
            }
        }

        let id = self.next_id;
        self.next_id += 1;

        let request = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });

        if let Err(err) = writeln!(self.stdin, "{}", request) {
            self.is_shutdown = true;
            return Err(BackendCallError::restartable(format!(
                "Python sidecar pipe closed while sending {method}: {err}"
            )));
        }

        if let Err(err) = self.stdin.flush() {
            self.is_shutdown = true;
            return Err(BackendCallError::restartable(format!(
                "Python sidecar pipe closed while flushing {method}: {err}"
            )));
        }

        loop {
            let mut line = String::new();

            let read = self.stdout.read_line(&mut line).map_err(|err| {
                BackendCallError::final_error(format!(
                    "failed to read Python sidecar response for {method}: {err}"
                ))
            })?;

            if read == 0 {
                self.is_shutdown = true;
                return Err(BackendCallError::final_error(format!(
                    "Python sidecar exited before responding to {method}"
                )));
            }

            let response: Value = serde_json::from_str(line.trim()).map_err(|err| {
                BackendCallError::final_error(format!(
                    "invalid Python sidecar JSON response for {method}: {err}: {line}"
                ))
            })?;

            if response.get("id").and_then(Value::as_u64) != Some(id) {
                continue;
            }

            if let Some(error) = response.get("error") {
                let message = error
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("sidecar error");

                return Err(BackendCallError::final_error(message.to_string()));
            }

            return Ok(response.get("result").cloned().unwrap_or(Value::Null));
        }
    }

    fn restart(&mut self, app: &AppHandle, profile_dir: &Path, reason: &str) -> Result<(), String> {
        let _ = self.shutdown();
        *self = Self::spawn(app, profile_dir)?;
        append_shell_log(
            profile_dir,
            &format!("Python sidecar restarted successfully after: {reason}"),
        );
        Ok(())
    }

    fn is_ready(&mut self) -> bool {
        if self.is_shutdown {
            return false;
        }

        match self.child.try_wait() {
            Ok(Some(_status)) => {
                self.is_shutdown = true;
                false
            }
            Ok(None) => true,
            Err(_err) => false,
        }
    }

    fn shutdown(&mut self) -> Result<(), String> {
        if !self.is_shutdown {
            let _ = self.call_once("app.shutdown", json!({}));
            self.is_shutdown = true;
        }

        let deadline = Instant::now() + Duration::from_secs(3);

        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_status)) => return Ok(()),
                Ok(None) => thread::sleep(Duration::from_millis(50)),
                Err(err) => return Err(err.to_string()),
            }
        }

        self.child.kill().map_err(|err| err.to_string())?;
        Ok(())
    }
}

#[tauri::command]
fn app_status(state: tauri::State<AppState>) -> AppStatus {
    let backend_ready = state
        .backend
        .lock()
        .map(|mut backend| backend.is_ready())
        .unwrap_or(false);

    AppStatus {
        profile_id: state.profile_id.clone(),
        profile_dir: state.profile_dir.display().to_string(),
        backend_ready,
    }
}

#[tauri::command]
fn rpc_call(
    method: String,
    params: Option<Value>,
    app: AppHandle,
    state: tauri::State<AppState>,
) -> Result<Value, String> {
    let mut backend = state
        .backend
        .lock()
        .map_err(|_| "backend lock is poisoned".to_string())?;

    backend.call_with_recovery(
        &app,
        &state.profile_dir,
        &method,
        params.unwrap_or_else(|| json!({})),
    )
}

#[tauri::command]
fn shutdown_backend(state: tauri::State<AppState>) -> Result<(), String> {
    let mut backend = state
        .backend
        .lock()
        .map_err(|_| "backend lock is poisoned".to_string())?;

    backend.shutdown()
}

fn append_shell_log(profile_dir: &Path, message: &str) {
    let logs_dir = profile_dir.join("logs");
    let _ = fs::create_dir_all(&logs_dir);
    let log_path = logs_dir.join("backend.log");
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default();

    if let Ok(mut file) = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
    {
        let _ = writeln!(file, "{timestamp} WARN odysseus_desktop.shell - {message}");
    }
}

fn ensure_default_profile(app: &AppHandle) -> Result<(String, PathBuf), String> {
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|err| format!("failed to locate app data dir: {err}"))?;

    let profile_id = "default".to_string();
    let profile_dir = app_data.join("profiles").join(&profile_id);

    fs::create_dir_all(profile_dir.join("logs"))
        .map_err(|err| format!("failed to create profile dir: {err}"))?;

    let profile_json = profile_dir.join("profile.json");

    if !profile_json.exists() {
        fs::write(
            &profile_json,
            json!({
                "id": profile_id,
                "name": "Default",
                "schema_version": 1
            })
            .to_string(),
        )
        .map_err(|err| format!("failed to write profile metadata: {err}"))?;
    }

    Ok((profile_id, profile_dir))
}

fn locate_python(app: &AppHandle) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    if let Ok(path) = std::env::var("ODYSSEUS_PYTHON") {
        let candidate = PathBuf::from(path);

        if candidate.exists() {
            return Ok(candidate);
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let embedded = resource_dir.join("python-runtime").join(if cfg!(windows) {
            "python.exe"
        } else {
            "bin/python3"
        });

        if embedded.exists() {
            return Ok(embedded);
        }
    }

    if cfg!(debug_assertions) {
        Ok(PathBuf::from(if cfg!(windows) {
            "python"
        } else {
            "python3"
        }))
    } else {
        Err("bundled python-runtime was not found in application resources".to_string())
    }
}

fn locate_sidecar_script(app: &AppHandle) -> Result<PathBuf, String> {
    let resource_script = app
        .path()
        .resource_dir()
        .map_err(|err| err.to_string())?
        .join("python")
        .join("rpc_server.py");

    if resource_script.exists() {
        return Ok(resource_script);
    }

    #[cfg(debug_assertions)]
    {
        let dev_script = std::env::current_dir()
            .map_err(|err| err.to_string())?
            .join("python")
            .join("rpc_server.py");

        if dev_script.exists() {
            return Ok(dev_script);
        }
    }

    Err("could not locate python/rpc_server.py".to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_handle = app.handle().clone();
            let (profile_id, profile_dir) = ensure_default_profile(&app_handle)?;
            let backend = BackendClient::spawn(&app_handle, &profile_dir)?;

            app.manage(AppState {
                profile_id,
                profile_dir,
                backend: Mutex::new(backend),
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            app_status,
            rpc_call,
            shutdown_backend
        ])
        .run(tauri::generate_context!())
        .expect("error while running Odysseus Desktop");
}
