#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::fs;
use std::path::PathBuf;

fn main() {
    // Finder/Dock launches only get launchd's PATH; same prefix as build-macos-app.sh.
    #[cfg(target_os = "macos")]
    {
        let path = std::env::var("PATH").unwrap_or_default();
        let path = format!("/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{path}");
        std::env::set_var("PATH", path);
    }

    let (is_installed, is_native) = check_installation_state(check_os());
    println!("{}", is_installed);
    odysseus_lib::run(is_installed, is_native);
}

fn check_os() -> &'static str {
    return std::env::consts::OS;
}

fn check_installation_state(os: &str) -> (bool, bool) {
    let home_var = match os {
        "windows" => "USERPROFILE",
        "macos" | "linux" => "HOME",
        _ => return (false, false),
    };

    let Some(home) = std::env::var_os(home_var) else {
        return (false, false);
    };

    let config_path = PathBuf::from(home)
        .join("Documents")
        .join("Odysseus Desktop")
        .join("config.json");

    let Ok(config_file) = fs::File::open(&config_path) else {
        return (false, false);
    };

    let config_json: serde_json::Value =
        serde_json::from_reader(config_file).unwrap_or(serde_json::Value::Null);

    return (
        config_json
            .get("installed")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        config_json
            .get("is_native")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
    );
}
