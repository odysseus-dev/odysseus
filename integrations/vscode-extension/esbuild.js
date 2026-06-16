const esbuild = require("esbuild");

const watch = process.argv.includes("--watch");

const shared = {
  bundle: true,
  minify: false,
  sourcemap: true,
  platform: "node",
  target: "node20",
  logLevel: "info",
};

async function run() {
  const extensionBuild = {
    ...shared,
    format: "cjs",
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    external: ["vscode"],
  };

  const webviewBuild = {
    ...shared,
    platform: "browser",
    format: "iife",
    target: "es2022",
    entryPoints: ["webview/main.ts"],
    outfile: "dist/webview.js",
  };

  if (watch) {
    const extensionContext = await esbuild.context(extensionBuild);
    const webviewContext = await esbuild.context(webviewBuild);
    await Promise.all([extensionContext.watch(), webviewContext.watch()]);
    console.log("Watching Odysseus VS Code extension sources...");
    return;
  }

  await Promise.all([esbuild.build(extensionBuild), esbuild.build(webviewBuild)]);
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
