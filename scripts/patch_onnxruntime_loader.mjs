import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const VERSION = "1.26.0";
const DEFAULT_AAR = path.join(
  os.homedir(),
  ".gradle",
  "caches",
  "modules-2",
  "files-2.1",
  "com.microsoft.onnxruntime",
  "onnxruntime-android",
  VERSION,
);

function findCachedAar(root) {
  const entries = fs.readdirSync(root, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const found = findCachedAar(fullPath);
      if (found) return found;
    } else if (entry.name === `onnxruntime-android-${VERSION}.aar`) {
      return fullPath;
    }
  }
  return null;
}

const aarPath = process.argv[2]
  ? path.resolve(process.argv[2])
  : findCachedAar(DEFAULT_AAR);
const outJar = path.resolve(
  process.argv[3] ??
    path.join(
      "android",
      "app",
      "libs",
      `onnxruntime-android-${VERSION}-patched-loader.jar`,
    ),
);

if (!aarPath || !fs.existsSync(aarPath)) {
  throw new Error(`ONNX Runtime AAR not found under ${DEFAULT_AAR}`);
}

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ort-patched-"));
try {
  execFileSync("jar", ["xf", aarPath], { cwd: tempRoot, stdio: "inherit" });

  const classesJar = path.join(tempRoot, "classes.jar");
  const classesDir = path.join(tempRoot, "classes");
  fs.mkdirSync(classesDir);
  execFileSync("jar", ["xf", classesJar], { cwd: classesDir, stdio: "inherit" });

  const onnxRuntimeClass = path.join(classesDir, "ai", "onnxruntime", "OnnxRuntime.class");
  const bytes = fs.readFileSync(onnxRuntimeClass);

  // In OnnxRuntime.load(String), the Android branch calls System.loadLibrary()
  // before the skip property is checked. Replacing the isAndroid() call with
  // iconst_0/nop/nop keeps bytecode length stable and routes Android through
  // the normal property-aware loader after we preload the native libs ourselves.
  const pattern = Buffer.from([
    0xb8, 0x00, 0x2d, 0x99, 0x00, 0x08, 0x2a, 0xb8, 0x01, 0x10, 0xb1,
  ]);
  const replacement = Buffer.from([
    0x03, 0x00, 0x00, 0x99, 0x00, 0x08, 0x2a, 0xb8, 0x01, 0x10, 0xb1,
  ]);
  const offset = bytes.indexOf(pattern);
  if (offset < 0) {
    throw new Error("Could not find expected ONNX Runtime loader bytecode pattern.");
  }
  replacement.copy(bytes, offset);
  fs.writeFileSync(onnxRuntimeClass, bytes);

  fs.mkdirSync(path.dirname(outJar), { recursive: true });
  if (fs.existsSync(outJar)) fs.rmSync(outJar);
  execFileSync("jar", ["cf", outJar, "."], { cwd: classesDir, stdio: "inherit" });

  const size = fs.statSync(outJar).size;
  console.log(JSON.stringify({ aarPath, outJar, size, patchOffset: offset }, null, 2));
} finally {
  fs.rmSync(tempRoot, { recursive: true, force: true });
}
