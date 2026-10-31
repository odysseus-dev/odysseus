//! Per-OS implementations of installation steps that can't be shared across platforms.
//! Each submodule exposes the same `install_git` / `launch_docker_desktop` API.

#[cfg(target_os = "windows")]
mod windows;
#[cfg(target_os = "windows")]
pub use windows::{install_git, launch_docker_desktop};

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
pub use macos::{install_git, launch_docker_desktop};

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::{install_git, launch_docker_desktop};
