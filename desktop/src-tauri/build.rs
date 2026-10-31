fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["install_update"])),
    )
    .expect("не удалось подготовить сборку");
}
