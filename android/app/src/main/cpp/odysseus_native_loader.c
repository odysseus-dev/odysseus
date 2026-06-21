#include <dlfcn.h>
#include <jni.h>

JNIEXPORT void JNICALL
Java_com_odysseus_simplesignal_NativeRuntimeLoader_dlopen(
        JNIEnv *env,
        jclass ignored,
        jstring absolutePath,
        jboolean global) {
    (void) ignored;

    const char *path = (*env)->GetStringUTFChars(env, absolutePath, 0);
    if (path == 0) return;

    int flags = RTLD_NOW;
    if (global) flags |= RTLD_GLOBAL;

    void *handle = dlopen(path, flags);
    const char *error = handle == 0 ? dlerror() : 0;
    (*env)->ReleaseStringUTFChars(env, absolutePath, path);

    if (handle == 0) {
        jclass errClass = (*env)->FindClass(env, "java/lang/UnsatisfiedLinkError");
        if (errClass != 0) {
            (*env)->ThrowNew(env, errClass, error == 0 ? "dlopen failed" : error);
        }
    }
}
