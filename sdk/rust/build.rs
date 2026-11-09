//! Generates the wire types from `schemas/v1` with a vendored `protoc`, so no system install is
//! needed.

use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let schemas = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schemas/v1");
    let files: Vec<PathBuf> = std::fs::read_dir(&schemas)?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.extension().is_some_and(|ext| ext == "proto"))
        .collect();
    for file in &files {
        println!("cargo:rerun-if-changed={}", file.display());
    }
    prost_build::Config::new()
        .protoc_executable(protoc_bin_vendored::protoc_bin_path()?)
        .btree_map(["."])
        .compile_protos(&files, &[schemas])?;
    Ok(())
}
