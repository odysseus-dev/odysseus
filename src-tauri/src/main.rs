#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::fs;
use std::path::PathBuf;

fn main() {
    let is_installed = check_installation_state(check_os());
    println!("{}", is_installed);
    odysseus_lib::run(is_installed);
}

fn check_os() -> &'static str {
    return std::env::consts::OS;
}

fn check_installation_state(os: &str) -> bool {
    let home_var = match os {
        "windows" => "USERPROFILE",
        "macos" | "linux" => "HOME",
        _ => return false,
    };

    let Some(home) = std::env::var_os(home_var) else {
        return false;
    };

    let config_path = PathBuf::from(home)
        .join("Documents")
        .join("Odysseus Desktop")
        .join("config.json");

    let Ok(config_file) = fs::File::open(&config_path) else {
        return false;
    };

    let config_json: serde_json::Value =
        serde_json::from_reader(config_file).unwrap_or(serde_json::Value::Null);

    config_json
        .get("installed")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}
