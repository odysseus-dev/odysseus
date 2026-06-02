use std::fs;
use std::path::PathBuf;

fn main() {
    // Generate an UNENCRYPTED key pair (no password)
    let key_pair = minisign::KeyPair::generate_unencrypted_keypair()
        .expect("Failed to generate key pair");

    let out_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_string()));
    let secret_key_path = out_dir.join("odysseus.key");
    let public_key_path = out_dir.join("odysseus.pub");

    // Write public key as box (text format with comment + base64)
    let pub_box = key_pair.pk.to_box().expect("Failed to create public key box");
    fs::write(&public_key_path, pub_box.to_bytes()).expect("Failed to write public key");

    // Write secret key as box (text format with comment + base64)
    // The unencrypted key uses a custom header
    let sk_box = key_pair.sk.to_box(None).expect("Failed to create secret key box");
    fs::write(&secret_key_path, sk_box.to_bytes()).expect("Failed to write secret key");

    println!("Secret key written to: {}", secret_key_path.display());
    println!("Public key written to: {}", public_key_path.display());

    // Read back the public key for display
    let pubkey_content = fs::read_to_string(&public_key_path).expect("Failed to read public key");
    let pubkey_line = pubkey_content.lines().skip(1).next().unwrap_or("").trim();
    println!("");
    println!("Add the following to your tauri.conf.json updater config:");
    println!("  \"pubkey\": \"{}\"", pubkey_line);
    println!("");
    println!("For GitHub Actions, set these secrets:");
    println!("  TAURI_SIGNING_PRIVATE_KEY = (the content of {})", secret_key_path.display());
    println!("  TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (leave empty for unencrypted)");
    println!("");
    println!("IMPORTANT: Keep the secret key private! Never commit it to git!");
}
