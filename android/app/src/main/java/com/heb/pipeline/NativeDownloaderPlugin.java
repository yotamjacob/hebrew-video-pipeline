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
    public void openDownloads(PluginCall call) {
        Intent intent = new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS);
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
