package com.odysseus.simplesignal;

final class NativeRuntimeLoader {
    static {
        System.loadLibrary("odysseus_native_loader");
    }

    private NativeRuntimeLoader() {
    }

    static native void dlopen(String absolutePath, boolean global);
}
