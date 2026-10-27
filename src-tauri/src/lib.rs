use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

struct Backend(Mutex<Option<CommandChild>>);

#[tauri::command]
fn get_api_token() -> Result<String, String> {
    // Same override the sidecar honours (config.get_data_dir); without it the shell read a
    // token from the default dir while the sidecar wrote it elsewhere, and every call got 401.
    let override_dir = std::env::var("APPLYQUEUE_DATA_DIR")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .map(std::path::PathBuf::from);
    let base = if let Some(dir) = override_dir {
        Some(dir)
    } else if cfg!(target_os = "macos") {
        // Must match backend/core/config.py get_data_dir() (APP_DIR_NAME).
        dirs::home_dir().map(|h| h.join("Library/Application Support/applyqueue"))
    } else if cfg!(target_os = "windows") {
        dirs::data_dir().map(|d| d.join("applyqueue"))
            .or_else(|| dirs::home_dir().map(|h| h.join("AppData").join("Roaming").join("applyqueue")))
    } else {
        dirs::home_dir().map(|h| h.join(".config/applyqueue"))
    }
    .ok_or("no home dir")?;

    let token_path = base.join(".api_token");

    // Backend sidecar may take a few seconds to start and write the token file.
    // Retry up to 30 times (30 seconds total) with 1-second intervals.
    for i in 0..30 {
        match std::fs::read_to_string(&token_path) {
            Ok(s) => {
                let trimmed = s.trim().to_string();
                if !trimmed.is_empty() {
                    return Ok(trimmed);
                }
            }
            Err(_) => {}
        }
        if i < 29 {
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
    }

    Err(format!("Token file not found after 30s: {}", token_path.display()))
}

/// Documents the UI may hand to the OS (tailored resumes and their text exports).
const OPENABLE_EXTENSIONS: &[&str] = &["pdf", "docx", "doc", "txt", "md", "rtf", "odt"];

/// Only an existing, absolute document file may be opened: never a URL, a directory or a
/// program, whatever string the webview sends.
fn checked_open_path(path: &str) -> Result<std::path::PathBuf, String> {
    let p = std::path::PathBuf::from(path.trim());
    if !p.is_absolute() {
        return Err(format!("Refusing to open a relative path: {path}"));
    }
    let ext = p
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase())
        .unwrap_or_default();
    if !OPENABLE_EXTENSIONS.contains(&ext.as_str()) {
        return Err(format!("Refusing to open this kind of file: {path}"));
    }
    if !p.is_file() {
        return Err(format!("File not found: {path}"));
    }
    Ok(p)
}

#[tauri::command]
fn open_file(path: String) -> Result<(), String> {
    let p = checked_open_path(&path)?;
    // The path goes to the OS as one argument (ShellExecuteW on Windows, `open` on macOS,
    // xdg-open/gio on Linux). The old Windows `cmd /C start "" <path>` let cmd re-parse the
    // path, so a name containing `&` or `^` was split into a second command.
    open::that_detached(&p).map_err(|e| format!("Failed to open file: {e}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_log::Builder::new().build())
        .manage(Backend(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![get_api_token, open_file])
        .setup(|app| {
            let shell = app.shell();
            match shell.sidecar("applyqueue-backend") {
                Ok(sidecar) => match sidecar
                    .args(["8743"])
                    // The backend watches this pid and exits if the window dies (see _launcher_pid).
                    .env("APPLYQUEUE_WINDOW_PID", std::process::id().to_string())
                    .spawn() {
                    Ok((_rx, child)) => {
                        log::info!("Backend sidecar started on port 8743");
                        *app.state::<Backend>().0.lock().unwrap() = Some(child);
                    }
                    Err(e) => log::error!("Failed to spawn backend sidecar: {e}"),
                },
                Err(e) => log::error!("Backend sidecar binary not found: {e}"),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app, _event| {
            // Sidecar lifecycle is self-managed:
            // - Parent watchdog thread in Python detects when Tauri exits
            // - SIGTERM handler cleans up browser processes
            // We don't kill the sidecar here because Tauri may fire Exit
            // before the sidecar finishes starting (observed on macOS M1)
        });
}

#[cfg(test)]
mod tests {
    use super::checked_open_path;

    fn tmp_file(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("aq-open-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join(name);
        std::fs::write(&p, b"%PDF-1.4").unwrap();
        p
    }

    #[test]
    fn accepts_an_existing_pdf_with_shell_metacharacters_in_its_name() {
        let p = tmp_file("Alex Example & Co ^ 100% resume.pdf");
        assert_eq!(checked_open_path(p.to_str().unwrap()).unwrap(), p);
    }

    #[test]
    fn extension_check_is_case_insensitive() {
        let p = tmp_file("Alex_Example.PDF");
        assert!(checked_open_path(p.to_str().unwrap()).is_ok());
    }

    #[test]
    fn refuses_relative_paths_urls_programs_dirs_and_missing_files() {
        assert!(checked_open_path("resume.pdf").is_err());
        assert!(checked_open_path("https://example.com/a.pdf").is_err());
        let exe = tmp_file("not-a-resume.exe");
        assert!(checked_open_path(exe.to_str().unwrap()).is_err());
        let sh = tmp_file("run.sh");
        assert!(checked_open_path(sh.to_str().unwrap()).is_err());
        let dir = std::env::temp_dir();
        assert!(checked_open_path(dir.to_str().unwrap()).is_err());
        let missing = std::env::temp_dir().join("aq-missing-Alex-Example.pdf");
        assert!(checked_open_path(missing.to_str().unwrap()).is_err());
    }
}
