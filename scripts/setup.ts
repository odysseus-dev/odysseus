#!/usr/bin/env node
import { spawn, execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import dotenv from "dotenv";

// Setup helpers and config
const
	rl = readline.createInterface({
		input: process.stdin,
		output: process.stdout,
	}),
	ask = (q: string): Promise<string> =>
		new Promise((r) => rl.question(q, r)),
	ROOT = path.resolve(__dirname, ".."),
	IS_WIN = process.platform === "win32",
	PYTHON = IS_WIN
		? (() => {
			try {
				return execSync("where python", { stdio: "pipe" })
					.toString().trim().split("\n")[0];
			} catch { return "python"; }
		})()
		: "python3",
	runSync = ({
		cmd,
		args,
		cwd,
		ignore
	}: {
		cmd: string;
		args?: string[];
		cwd?: string;
		ignore?: boolean;
	}): boolean => {
		try {
			execSync(`${cmd} ${(args || []).join(" ")}`, {
				cwd: cwd ?? ROOT,
				stdio: ignore ? "ignore" : "inherit",
			});
			return true;
		} catch {
			return false;
		}
	},
	elapsed = (start: number): string =>
		`${Math.floor((Date.now() - start) / 1000)}s`,
	main = async () => {

		// Main entry point
		try {
			console.log(
				"\n" +
				"╔══════════════════════════════════════════╗\n" +
				"║         Odysseus — Setup & Launch        ║\n" +
				"╚══════════════════════════════════════════╝\n" +
				"\n" +
				"  Platform:       " + (
					IS_WIN ? "WINDOWS"
						: process.platform === "darwin" ? "MACOS"
							: process.platform.toUpperCase()
				) + "\n" +
				"  Python:         " + PYTHON + "\n"
			);

			// Load existing config or ask for server port
			dotenv.config();
			let PORT = process.env.ODYSSEUS_PORT || "";
			if (!PORT) {
				const ans = await ask("  Server port (default: 7000): ");
				PORT = ans.trim() || "7000";
				fs.appendFileSync(
					path.join(ROOT, ".env"),
					`\nODYSSEUS_PORT=${JSON.stringify(PORT)}\n`
				);
			}
			console.log();

			// Ask for admin credentials (first run only)
			const authPath = path.join(ROOT, "data", "auth.json");
			let
				adminUser = "admin",
				adminPass = "";

			if (!fs.existsSync(authPath)) {
				console.log("  === Admin Account ===");
				adminUser = (
					await ask("  Admin username (default: admin): ")
				).trim() || "admin";

				while (true) {
					const
						pw1 = await ask("  Admin password (min 8 chars): "),
						pw2 = await ask("  Confirm password: ");
					if (pw1 !== pw2) {
						console.log("  Passwords do not match. Try again.");
					} else if (pw1.length < 8) {
						console.log("  Password must be at least 8 characters. Try again.");
					} else {
						adminPass = pw1;
						break;
					}
				}
				console.log();
			}

			// Helper functions for progress steps
			const
				step = ({ n, label }: {
					n: number;
					label: string;
				}): number => {
					process.stdout.write(`    [${n}/5] ${label}... `);
					return Date.now();
				},
				done = (t: number) => console.log(`\x1b[32mDONE\x1b[0m (${elapsed(t)})`);

			let t = step({ n: 1, label: "Install system dependencies" });

			// Step 1: Install system dependencies (Linux only)
			if (!IS_WIN) {
				const missing: string[] = [];
				if (!runSync({
					cmd: "command", args: ["-v", "python3"], ignore: true
				})) missing.push("python3");
				if (!runSync({
					cmd: "command", args: ["-v", "tmux"], ignore: true
				})) missing.push("tmux");
				if (!runSync({
					cmd: "python3", args: ["-c", "import venv"], ignore: true
				})) missing.push("python3-venv");

				if (missing.length > 0) {
					const pm =
						["apt", "dnf", "pacman", "zypper", "apk", "brew"].find(
							(p) => runSync({ cmd: "command", args: ["-v", p], ignore: true })
						) || null;

					if (pm) {
						const cmds: Record<string, string[]> = {
							apt: [`sudo apt update && sudo apt install -y ${missing.join(" ")}`],
							dnf: ["sudo dnf install -y python3 tmux"],
							pacman: ["sudo pacman -S --noconfirm python tmux"],
							zypper: ["sudo zypper install -y python3 tmux python3-venv"],
							apk: ["sudo apk add python3 py3-pip tmux"],
							brew: ["brew install python3 tmux"],
						};
						for (const c of cmds[pm] || []) runSync({ cmd: "bash", args: ["-c", c] });
					}
				}
			}
			done(t);

			// Step 2: Create Python virtual env and install packages
			t = step({ n: 2, label: "Python virtual environment" });
			const
				venvPath = path.join(ROOT, "venv");
			if (!fs.existsSync(venvPath))
				execSync(`${PYTHON} -m venv venv`, { stdio: "inherit", cwd: ROOT });
			const
				pip = IS_WIN
					? path.join(venvPath, "Scripts", "pip")
					: path.join(venvPath, "bin", "pip");
			execSync(
				`"${pip}" install -q -r requirements.txt`,
				{ stdio: "inherit", cwd: ROOT }
			);
			done(t);

			// Step 3: Save admin credentials to .env
			t = step({ n: 3, label: "Admin account" });
			if (adminPass && adminUser) {
				dotenv.config({ override: true });
				for (const [key, val] of [
					["ODYSSEUS_ADMIN_USER", adminUser] as const,
					["ODYSSEUS_ADMIN_PASSWORD", adminPass] as const,
				])
					if (!process.env[key])
						fs.appendFileSync(
							path.join(ROOT, ".env"),
							`\n${key}=${JSON.stringify(val)}\n`
						);
				process.env.ODYSSEUS_ADMIN_USER = adminUser;
				process.env.ODYSSEUS_ADMIN_PASSWORD = adminPass;
			}
			done(t);

			// Step 4: Run database setup
			t = step({ n: 4, label: "Database & config" });
			const
				python = IS_WIN
					? path.join(venvPath, "Scripts", "python")
					: path.join(venvPath, "bin", "python");
			execSync(`"${python}" setup.py`, { stdio: "inherit", cwd: ROOT });
			done(t);

			// Step 5: Launch the web server
			step({ n: 5, label: `Start server on port ${PORT}` });
			console.log("\n");
			spawn(
				python,
				["-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", PORT],
				{
					stdio: "inherit",
					cwd: ROOT,
				}
			);
		} catch (e) {

			// Handle errors gracefully
			console.error(e instanceof Error ? e.message : e);
			process.exit(1);
		}
	};

main();
