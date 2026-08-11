package com.heb.pipeline;

import android.Manifest;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.io.File;

@CapacitorPlugin(
    name = "NativeDownloader",
    permissions = @Permission(
        strings = { Manifest.permission.WRITE_EXTERNAL_STORAGE },
        alias = NativeDownloaderPlugin.LEGACY_STORAGE
    )
)
public class NativeDownloaderPlugin extends Plugin {
    static final String LEGACY_STORAGE = "legacyStorage";

    @PluginMethod
    public void download(PluginCall call) {
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
            && getPermissionState(LEGACY_STORAGE) != PermissionState.GRANTED) {
            requestPermissionForAlias(LEGACY_STORAGE, call, "legacyStoragePermission");
            return;
        }
        enqueue(call);
    }

    @PermissionCallback
    private void legacyStoragePermission(PluginCall call) {
        if (getPermissionState(LEGACY_STORAGE) != PermissionState.GRANTED) {
            call.reject("Downloads storage permission was denied");
            return;
        }
        enqueue(call);
    }

    private void enqueue(PluginCall call) {
        String url = call.getString("url");
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) {
            call.reject("A valid HTTP download URL is required");
            return;
        }

        String filename = availableFilename(
            sanitizeFilename(call.getString("filename", "video.mp4")));
        String mimeType = call.getString("mimeType", "video/mp4");
        String description = call.getString("description", "Downloading video");
        DownloadManager manager =
            (DownloadManager) getContext().getSystemService(Context.DOWNLOAD_SERVICE);
        if (manager == null) {
            call.reject("Android Download Manager is unavailable");
            return;
        }

        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url))
                .setTitle(filename)
                .setDescription(description)
                .setMimeType(mimeType)
                .setAllowedOverMetered(true)
                .setAllowedOverRoaming(true)
                .setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS, filename);
            if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
                request.allowScanningByMediaScanner();
                request.setVisibleInDownloadsUi(true);
            }

            long id = manager.enqueue(request);
            JSObject result = new JSObject();
            result.put("id", id);
            result.put("filename", filename);
            result.put("location", "Downloads/" + filename);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("Could not start Android download", error);
        }
    }

    @PluginMethod
    public void openFile(PluginCall call) {
        // Open the downloaded VIDEO itself (field report 2026-08-09: the toast
        // link surfaced a folder/share UI instead of the file). The id comes
        // from download()'s resolve payload; getUriForDownloadedFile returns a
        // content:// URI only once the download SUCCEEDED, so an in-flight or
        // failed download falls back to the system Downloads UI - never an
        // ACTION_VIEW on a half-written file.
        Long id = call.getLong("id", -1L);
        String mimeType = call.getString("mimeType", "video/mp4");
        DownloadManager manager =
            (DownloadManager) getContext().getSystemService(Context.DOWNLOAD_SERVICE);
        Uri uri = (manager != null && id != null && id >= 0)
            ? manager.getUriForDownloadedFile(id) : null;
        if (uri == null) {
            openDownloads(call);
            return;
        }
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, mimeType)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            getActivity().startActivity(intent);
            call.resolve();
        } catch (Exception error) {
            call.reject("Could not open the downloaded file", error);
        }
    }

    @PluginMethod
    public void saveToDownloads(PluginCall call) {
        // Save an ALREADY-DOWNLOADED file (the share warm-up's cache copy) into
        // public Downloads, without fetching it a second time.
        //
        // This is the fast path that Filesystem.copy used to provide until
        // 2026-08-11, when it turned out `copy` never registers the file with
        // MediaStore: the video was on disk but invisible to every file manager
        // (field report: "not saved to device"). Here the MediaStore row IS the
        // write on API 29+, so the result is indexed by construction.
        //
        // A large video must not be copied on the caller's thread, so the work
        // runs on its own thread and resolves from there.
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
            && getPermissionState(LEGACY_STORAGE) != PermissionState.GRANTED) {
            requestPermissionForAlias(LEGACY_STORAGE, call, "legacySavePermission");
            return;
        }
        new Thread(() -> copyIntoDownloads(call)).start();
    }

    @PermissionCallback
    private void legacySavePermission(PluginCall call) {
        if (getPermissionState(LEGACY_STORAGE) != PermissionState.GRANTED) {
            call.reject("Downloads storage permission was denied");
            return;
        }
        new Thread(() -> copyIntoDownloads(call)).start();
    }

    private void copyIntoDownloads(PluginCall call) {
        String source = call.getString("path");
        String filename = sanitizeFilename(call.getString("filename", "video.mp4"));
        String mimeType = call.getString("mimeType", "video/mp4");
        String title = call.getString("title", filename);
        String text = call.getString("text", "Tap to watch");
        if (source == null || source.isEmpty()) {
            call.reject("A source path is required");
            return;
        }
        try {
            Uri saved;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                // MediaStore both writes and indexes; it also de-duplicates the
                // display name itself, so availableFilename is not needed here.
                android.content.ContentValues values = new android.content.ContentValues();
                values.put(android.provider.MediaStore.Downloads.DISPLAY_NAME, filename);
                values.put(android.provider.MediaStore.Downloads.MIME_TYPE, mimeType);
                values.put(android.provider.MediaStore.Downloads.IS_PENDING, 1);
                android.content.ContentResolver resolver = getContext().getContentResolver();
                saved = resolver.insert(
                    android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (saved == null) {
                    call.reject("Could not create the Downloads entry");
                    return;
                }
                try (java.io.InputStream in = openSource(source);
                     java.io.OutputStream out = resolver.openOutputStream(saved)) {
                    pump(in, out);
                }
                values.clear();
                values.put(android.provider.MediaStore.Downloads.IS_PENDING, 0);
                resolver.update(saved, values, null, null);
            } else {
                // Pre-Q has no MediaStore Downloads collection: write the file
                // and hand it to the scanner so it still shows up.
                File target = new File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                    availableFilename(filename));
                try (java.io.InputStream in = openSource(source);
                     java.io.OutputStream out = new java.io.FileOutputStream(target)) {
                    pump(in, out);
                }
                android.media.MediaScannerConnection.scanFile(
                    getContext(), new String[] { target.getAbsolutePath() },
                    new String[] { mimeType }, null);
                saved = Uri.fromFile(target);
            }
            notifySavedFile(saved, mimeType, title, text);
            JSObject result = new JSObject();
            result.put("uri", saved.toString());
            result.put("filename", filename);
            call.resolve(result);
        } catch (Exception error) {
            // The JS caller falls back to DownloadManager on any rejection, so
            // a failure here costs a re-download, never the file.
            call.reject("Could not save into Downloads", error);
        }
    }

    private java.io.InputStream openSource(String source) throws Exception {
        Uri uri = Uri.parse(source);
        String scheme = uri.getScheme();
        if ("content".equals(scheme)) {
            return getContext().getContentResolver().openInputStream(uri);
        }
        return new java.io.FileInputStream(
            "file".equals(scheme) ? new File(uri.getPath()) : new File(source));
    }

    private void pump(java.io.InputStream in, java.io.OutputStream out) throws Exception {
        if (in == null || out == null) throw new java.io.IOException("Unreadable source file");
        byte[] buffer = new byte[128 * 1024];
        int read;
        while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
        out.flush();
    }

    private void notifySavedFile(Uri uri, String mimeType, String title, String text) {
        // The DownloadManager path gets Android's own completion notification;
        // this path writes the file itself, so it has to post its own. Tapping
        // opens the video (a raw file:// would throw FileUriExposedException on
        // N+, hence the FileProvider). Best-effort: without a POST_NOTIFICATIONS
        // grant, or on any failure, the in-app toast already covers the UX.
        try {
            Uri viewUri = uri;
            if ("file".equals(viewUri.getScheme())) {
                viewUri = androidx.core.content.FileProvider.getUriForFile(
                    getContext(), getContext().getPackageName() + ".fileprovider",
                    new File(viewUri.getPath()));
            }
            Intent view = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(viewUri, mimeType)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            android.app.PendingIntent pending = android.app.PendingIntent.getActivity(
                getContext(), (int) (System.currentTimeMillis() & 0x7fffffff), view,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT
                    | android.app.PendingIntent.FLAG_IMMUTABLE);
            android.app.NotificationManager nm = (android.app.NotificationManager)
                getContext().getSystemService(Context.NOTIFICATION_SERVICE);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                nm.createNotificationChannel(new android.app.NotificationChannel(
                    "downloads", "Downloads",
                    android.app.NotificationManager.IMPORTANCE_DEFAULT));
            }
            androidx.core.app.NotificationCompat.Builder builder =
                new androidx.core.app.NotificationCompat.Builder(getContext(), "downloads")
                    .setSmallIcon(getContext().getApplicationInfo().icon)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setContentIntent(pending)
                    .setAutoCancel(true);
            nm.notify((int) (System.currentTimeMillis() & 0x7fffffff), builder.build());
        } catch (Exception ignored) {
            // Missing permission, unparseable uri, no provider path - all fine.
        }
    }

    @PluginMethod
    public void openDownloads(PluginCall call) {
        // ACTION_VIEW_DOWNLOADS is an OEM lottery: some shells have no
        // matching activity and some resolve it to a STORE download UI
        // (field report: tapping "Open Downloads" opened the Play Store).
        // Prefer it only when a real handler exists, otherwise open the
        // system Files app (API 29+) which always shows Downloads.
        Intent intent = new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS);
        boolean handled = intent.resolveActivity(getContext().getPackageManager()) != null;
        if (!handled && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            intent = Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, Intent.CATEGORY_APP_FILES);
        }
        try {
            getActivity().startActivity(intent);
            call.resolve();
        } catch (Exception error) {
            call.reject("Could not open Android Downloads", error);
        }
    }

    static String sanitizeFilename(String raw) {
        String cleaned = raw == null ? "" : raw
            .replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]+", "_")
            .replaceFirst("^\\.+", "")
            .trim();
        if (cleaned.isEmpty()) cleaned = "video.mp4";
        return cleaned.length() <= 180 ? cleaned : cleaned.substring(0, 180);
    }

    private String availableFilename(String preferred) {
        File downloads =
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
        if (!new File(downloads, preferred).exists()) return preferred;

        int dot = preferred.lastIndexOf('.');
        String stem = dot > 0 ? preferred.substring(0, dot) : preferred;
        String extension = dot > 0 ? preferred.substring(dot) : "";
        for (int copy = 2; copy < 10_000; copy++) {
            String candidate = stem + " (" + copy + ")" + extension;
            if (!new File(downloads, candidate).exists()) return candidate;
        }
        return stem + "-" + System.currentTimeMillis() + extension;
    }
}
