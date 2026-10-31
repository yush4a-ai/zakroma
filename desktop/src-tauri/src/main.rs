// Закрома для Windows: главное окно, окно быстрого поиска, иконка в трее и горячие клавиши.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

fn show(app: &AppHandle, label: &str) {
    if let Some(w) = app.get_webview_window(label) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn toggle_quick(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("quick") {
        if w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false) {
            let _ = w.hide();
        } else {
            let _ = w.center();
            show(app, "quick");
            let _ = w.emit("quick-open", ());
        }
    }
}

/// Скачивает установщик новой версии с GitHub, тихо ставит его и перезапускает программу.
/// Принимает только ссылки на релизы нашего репозитория — ничего другого скачать и запустить нельзя.
#[tauri::command]
fn install_update(app: AppHandle, url: String) -> Result<(), String> {
    const ALLOWED: &str = "https://github.com/yush4a-ai/zakroma/releases/download/";
    if !url.starts_with(ALLOWED) || !url.ends_with("-setup.exe") || url.contains('\'') || url.contains('"') {
        return Err("Недопустимая ссылка на обновление".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let exe = exe.to_string_lossy().replace('\'', "''");
        let script = format!(
            "$ErrorActionPreference='Stop'; $f = Join-Path $env:TEMP 'zakroma-update.exe'; \
             Invoke-WebRequest -UseBasicParsing -Uri '{url}' -OutFile $f; \
             Start-Sleep -Seconds 1; Start-Process -Wait -FilePath $f -ArgumentList '/S'; \
             Start-Process -FilePath '{exe}'"
        );
        std::process::Command::new("powershell")
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", &script])
            .creation_flags(0x08000000) // без чёрного окна консоли
            .spawn()
            .map_err(|e| e.to_string())?;
        // даём установщику заменить файлы — закрываемся
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(800));
            app.exit(0);
        });
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = app;
        Err("Автообновление работает только в Windows".into())
    }
}

fn main() {
    let ctrl_shift = Some(Modifiers::CONTROL | Modifiers::SHIFT);
    // Ctrl+Shift+Z — быстрый поиск, Ctrl+Shift+S — сохранить из буфера,
    // Ctrl+Shift+D — разобрать, Ctrl+Alt+Z — открыть приложение
    let quick = Shortcut::new(ctrl_shift, Code::KeyZ);
    let save = Shortcut::new(ctrl_shift, Code::KeyS);
    let triage = Shortcut::new(ctrl_shift, Code::KeyD);
    let open = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyZ);
    let (q, s, t, o) = (quick.clone(), save.clone(), triage.clone(), open.clone());

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![install_update])
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show(app, "main")))
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--hidden"])))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, sc, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    if sc == &q {
                        toggle_quick(app);
                    } else if sc == &s {
                        let text = app.clipboard().read_text().unwrap_or_default();
                        let _ = app.emit_to("main", "quick-save", text);
                    } else if sc == &t {
                        show(app, "main");
                        let _ = app.emit_to("main", "open-triage", ());
                    } else if sc == &o {
                        show(app, "main");
                    }
                })
                .build(),
        )
        .setup(move |app| {
            for sc in [quick, save, triage, open] {
                // если клавиша занята другой программой — просто пропускаем её
                let _ = app.global_shortcut().register(sc);
            }

            // при автозапуске с Windows стартуем свёрнутыми в трей
            if std::env::args().any(|a| a == "--hidden") {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }

            let open_i = MenuItem::with_id(app, "open", "Открыть Закрома", true, None::<&str>)?;
            let quick_i = MenuItem::with_id(app, "quick", "Быстрый поиск  Ctrl+Shift+Z", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &quick_i, &quit_i])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Закрома — Ctrl+Shift+Z")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "open" => show(app, "main"),
                    "quick" => toggle_quick(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, e| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = e
                    {
                        show(tray.app_handle(), "main");
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // крестик прячет окно в трей, а не закрывает программу
            WindowEvent::CloseRequested { api, .. } => {
                let _ = window.hide();
                api.prevent_close();
            }
            // окно быстрого поиска прячется, как только уходит фокус
            WindowEvent::Focused(false) if window.label() == "quick" => {
                let _ = window.hide();
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("не удалось запустить Закрома");
}
