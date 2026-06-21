package com.odysseus.simplesignal;

import android.content.Context;
import android.os.Build;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.FloatBuffer;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

final class DynamicOnnxRuntime implements AutoCloseable {
    static final String VERSION = "1.26.0";
    static final String PACKAGE_NAME = "onnxruntime-android";
    static final String AAR_URL = "https://repo1.maven.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/"
            + VERSION + "/onnxruntime-android-" + VERSION + ".aar";
    static final long AAR_EXPECTED_BYTES = 43596581L;

    private static boolean nativeLibrariesLoaded = false;

    private Class<?> environmentClass;
    private Class<?> sessionOptionsClass;
    private Class<?> tensorClass;
    private Object environment;
    private Object session;
    private String sessionModelPath = "";

    static boolean isInstalled(Context context) {
        File root = runtimeRoot(context);
        return new File(nativeLibDir(root), "libonnxruntime.so").isFile()
                && new File(nativeLibDir(root), "libonnxruntime4j_jni.so").isFile();
    }

    static String statusNote(Context context) {
        File root = runtimeRoot(context);
        if (isInstalled(context)) {
            return root.getAbsolutePath() + " (" + String.format(java.util.Locale.US, "%.1f MB", installedBytes(context) / 1048576.0) + ")";
        }
        return "Downloads ONNX Runtime Android " + VERSION + " from Maven Central, then installs only this device ABI.";
    }

    static long installedBytes(Context context) {
        return dirSize(runtimeRoot(context));
    }

    static JSONObject install(Context context) throws Exception {
        File root = runtimeRoot(context);
        File parent = root.getParentFile();
        if (parent == null) {
            throw new IllegalStateException("Runtime cache path is unavailable.");
        }
        if (!parent.exists() && !parent.mkdirs()) {
            throw new IllegalStateException("Could not create runtime cache directory.");
        }

        File staging = new File(parent, root.getName() + ".installing");
        File aar = new File(parent, PACKAGE_NAME + "-" + VERSION + ".aar.download");
        deleteRecursive(staging);
        if (!staging.mkdirs()) {
            throw new IllegalStateException("Could not create runtime staging directory.");
        }
        try {
            downloadFile(AAR_URL, aar);
            extractRuntimeAar(aar, staging);
            if (!isInstalledAt(staging)) {
                throw new IllegalStateException("Downloaded runtime did not contain the required ONNX files.");
            }
            deleteRecursive(root);
            if (!staging.renameTo(root)) {
                throw new IllegalStateException("Could not activate downloaded ONNX runtime.");
            }
            return new JSONObject()
                    .put("ok", true)
                    .put("name", PACKAGE_NAME)
                    .put("version", VERSION)
                    .put("path", root.getAbsolutePath())
                    .put("bytes", installedBytes(context));
        } finally {
            deleteRecursive(staging);
            if (aar.exists()) {
                //noinspection ResultOfMethodCallIgnored
                aar.delete();
            }
        }
    }

    synchronized float[] runMask(Context context, File model, float[] input, int inputSize) throws Exception {
        ensureLoaded(context);
        ensureSession(model);

        String inputName = firstName(session.getClass().getMethod("getInputNames").invoke(session));
        String outputName = firstName(session.getClass().getMethod("getOutputNames").invoke(session));
        Method createTensor = tensorClass.getMethod("createTensor", environmentClass, FloatBuffer.class, long[].class);
        Object tensor = createTensor.invoke(null, environment, FloatBuffer.wrap(input), new long[]{1, 3, inputSize, inputSize});
        Object result = null;
        try {
            Map<String, Object> inputs = new HashMap<>();
            inputs.put(inputName, tensor);
            result = session.getClass().getMethod("run", Map.class).invoke(session, inputs);
            Object value = optionalValue(result.getClass().getMethod("get", String.class).invoke(result, outputName));
            if (value == null) {
                value = optionalValue(result.getClass().getMethod("get", int.class).invoke(result, 0));
            }
            if (value == null) return null;
            Object raw = value.getClass().getMethod("getValue").invoke(value);
            return flattenMask(raw, inputSize, inputSize);
        } finally {
            closeQuietly(result);
            closeQuietly(tensor);
        }
    }

    private void ensureLoaded(Context context) throws Exception {
        if (environmentClass != null) return;
        if (!isInstalled(context)) {
            throw new IllegalStateException("ONNX Runtime Android is not installed. Install it from Cookbook Dependencies.");
        }
        File root = runtimeRoot(context);
        lockRuntimeFiles(root);
        File nativeDir = nativeLibDir(root);
        loadNativeLibraries(nativeDir);
        ClassLoader appLoader = context.getClassLoader();
        environmentClass = Class.forName("ai.onnxruntime.OrtEnvironment", true, appLoader);
        sessionOptionsClass = Class.forName("ai.onnxruntime.OrtSession$SessionOptions", true, appLoader);
        tensorClass = Class.forName("ai.onnxruntime.OnnxTensor", true, appLoader);
        environment = environmentClass.getMethod("getEnvironment").invoke(null);
    }

    private void ensureSession(File model) throws Exception {
        String path = model.getAbsolutePath();
        if (session != null && path.equals(sessionModelPath)) return;
        closeQuietly(session);
        session = null;
        sessionModelPath = "";
        Object options = sessionOptionsClass.getConstructor().newInstance();
        try {
            session = environmentClass
                    .getMethod("createSession", String.class, sessionOptionsClass)
                    .invoke(environment, path, options);
            sessionModelPath = path;
        } finally {
            closeQuietly(options);
        }
    }

    private static Object optionalValue(Object optional) {
        if (!(optional instanceof Optional<?>)) return null;
        Optional<?> opt = (Optional<?>) optional;
        return opt.orElse(null);
    }

    private static String firstName(Object names) {
        if (!(names instanceof Set<?>)) return "";
        for (Object name : (Set<?>) names) {
            if (name != null) return String.valueOf(name);
        }
        return "";
    }

    private static float[] flattenMask(Object output, int width, int height) {
        int count = width * height;
        float[] mask = new float[count];
        if (output instanceof float[][][][]) {
            float[][][][] arr = (float[][][][]) output;
            if (arr.length == 0 || arr[0].length == 0) return null;
            for (int y = 0; y < height; y++) {
                System.arraycopy(arr[0][0][y], 0, mask, y * width, width);
            }
            return mask;
        }
        if (output instanceof float[][][]) {
            float[][][] arr = (float[][][]) output;
            if (arr.length == 0) return null;
            for (int y = 0; y < height; y++) {
                System.arraycopy(arr[0][y], 0, mask, y * width, width);
            }
            return mask;
        }
        if (output instanceof float[][]) {
            float[][] arr = (float[][]) output;
            for (int y = 0; y < height; y++) {
                System.arraycopy(arr[y], 0, mask, y * width, width);
            }
            return mask;
        }
        return null;
    }

    @Override
    public synchronized void close() {
        closeQuietly(session);
        closeQuietly(environment);
        session = null;
        environment = null;
        sessionModelPath = "";
    }

    private static void extractRuntimeAar(File aar, File staging) throws Exception {
        try (ZipFile zip = new ZipFile(aar)) {
            String abi = selectAbi(zip);
            if (abi.isEmpty()) {
                throw new IllegalStateException("ONNX Runtime does not include a native library for this device ABI.");
            }
            File nativeDir = nativeLibDir(staging);
            if (!nativeDir.exists() && !nativeDir.mkdirs()) {
                throw new IllegalStateException("Could not create native runtime directory.");
            }
            extractEntry(zip, "jni/" + abi + "/libonnxruntime.so", new File(nativeDir, "libonnxruntime.so"));
            extractEntry(zip, "jni/" + abi + "/libonnxruntime4j_jni.so", new File(nativeDir, "libonnxruntime4j_jni.so"));
        }
    }

    private static String selectAbi(ZipFile zip) {
        for (String abi : Build.SUPPORTED_ABIS) {
            if (zip.getEntry("jni/" + abi + "/libonnxruntime.so") != null
                    && zip.getEntry("jni/" + abi + "/libonnxruntime4j_jni.so") != null) {
                return abi;
            }
        }
        return "";
    }

    private static void extractEntry(ZipFile zip, String name, File out) throws Exception {
        ZipEntry entry = zip.getEntry(name);
        if (entry == null) {
            throw new IllegalStateException("Missing " + name + " in ONNX Runtime package.");
        }
        File parent = out.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IllegalStateException("Could not create " + parent.getAbsolutePath());
        }
        try (InputStream input = zip.getInputStream(entry);
             FileOutputStream output = new FileOutputStream(out)) {
            byte[] buf = new byte[32768];
            int n;
            while ((n = input.read(buf)) >= 0) {
                output.write(buf, 0, n);
            }
        }
        if (out.length() <= 0) {
            throw new IllegalStateException("Extracted empty runtime file: " + name);
        }
        lockRuntimeFile(out);
    }

    private static void downloadFile(String url, File out) throws Exception {
        File parent = out.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IllegalStateException("Could not create " + parent.getAbsolutePath());
        }
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(20000);
            conn.setReadTimeout(300000);
            conn.setRequestProperty("Accept", "application/java-archive,application/octet-stream,*/*");
            int status = conn.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IllegalStateException("Runtime download failed with HTTP " + status);
            }
            try (InputStream input = conn.getInputStream();
                 FileOutputStream output = new FileOutputStream(out)) {
                byte[] buf = new byte[32768];
                int n;
                while ((n = input.read(buf)) >= 0) {
                    output.write(buf, 0, n);
                }
            }
            if (out.length() < Math.max(1L, AAR_EXPECTED_BYTES / 2L)) {
                throw new IllegalStateException("Runtime download was incomplete.");
            }
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static boolean isInstalledAt(File root) {
        return new File(nativeLibDir(root), "libonnxruntime.so").isFile()
                && new File(nativeLibDir(root), "libonnxruntime4j_jni.so").isFile();
    }

    private static File runtimeRoot(Context context) {
        return new File(new File(context.getFilesDir(), "runtime"), PACKAGE_NAME + "-" + VERSION);
    }

    private static File nativeLibDir(File root) {
        return new File(root, "lib");
    }

    private static void lockRuntimeFiles(File root) {
        File libDir = nativeLibDir(root);
        lockRuntimeFile(new File(libDir, "libonnxruntime.so"));
        lockRuntimeFile(new File(libDir, "libonnxruntime4j_jni.so"));
    }

    private static synchronized void loadNativeLibraries(File nativeDir) {
        if (nativeLibrariesLoaded) return;
        System.setProperty("onnxruntime.native.onnxruntime.skip", "true");
        System.setProperty("onnxruntime.native.onnxruntime4j_jni.skip", "true");
        NativeRuntimeLoader.dlopen(new File(nativeDir, "libonnxruntime.so").getAbsolutePath(), true);
        System.load(new File(nativeDir, "libonnxruntime4j_jni.so").getAbsolutePath());
        nativeLibrariesLoaded = true;
    }

    private static void lockRuntimeFile(File file) {
        if (file == null || !file.exists()) return;
        // Android 14+ rejects writable dynamically-loaded dex/jar files.
        // Native libraries are made non-writable too so the installed module
        // is immutable after download/extraction.
        //noinspection ResultOfMethodCallIgnored
        file.setReadable(true, true);
        //noinspection ResultOfMethodCallIgnored
        file.setWritable(false, false);
        //noinspection ResultOfMethodCallIgnored
        file.setExecutable(file.getName().endsWith(".so"), true);
    }

    private static long dirSize(File file) {
        if (file == null || !file.exists()) return 0L;
        if (file.isFile()) return file.length();
        long total = 0L;
        File[] children = file.listFiles();
        if (children == null) return 0L;
        for (File child : children) total += dirSize(child);
        return total;
    }

    private static void deleteRecursive(File file) {
        if (file == null || !file.exists()) return;
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) deleteRecursive(child);
            }
        }
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }

    private static void closeQuietly(Object value) {
        if (value == null) return;
        try {
            if (value instanceof AutoCloseable) {
                ((AutoCloseable) value).close();
                return;
            }
            value.getClass().getMethod("close").invoke(value);
        } catch (Exception ignored) {
        }
    }
}
