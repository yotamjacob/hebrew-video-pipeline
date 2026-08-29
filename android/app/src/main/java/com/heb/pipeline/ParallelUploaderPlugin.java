package com.heb.pipeline;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.Map;
import java.util.UUID;
import org.json.JSONObject;

/**
 * Parallel multipart upload to presigned R2 part URLs, run inside a
 * foreground service (survives minimize/lock, owns a progress notification).
 *
 * Why this exists: the stock @capgo uploader sends ONE connection, and a
 * single TCP stream on the ~93 ms path to R2 tops out well under the uplink
 * (field: 9 vs 21 Mbps). N concurrent byte-range PUTs fill the pipe like the
 * web uploader's 6 parallel connections do.
 *
 * The event contract deliberately mirrors @capgo/capacitor-uploader
 * ({name, id, eventId, payload}, persisted terminal events + acknowledgeEvent)
 * so the web app drives both plugins with the same code.
 */
@CapacitorPlugin(name = "ParallelUploader")
public class ParallelUploaderPlugin extends Plugin {

    static final String PREFS = "hebpipe_parallel_uploader_events";
    private static ParallelUploaderPlugin instance;

    @Override
    public void load() {
        instance = this;
        // Replay terminal events that fired while the WebView was dead or
        // frozen - same recovery contract as the stock uploader. They stay
        // persisted until JS acknowledges them.
        SharedPreferences sp = prefs(getContext());
        for (Map.Entry<String, ?> e : sp.getAll().entrySet()) {
            try {
                notifyListeners("events", JSObject.fromJSONObject(
                    new JSONObject(String.valueOf(e.getValue()))));
            } catch (Exception ignored) { }
        }
    }

    static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Called by the service. Terminal events are persisted first, so a dead
     *  WebView can still learn the outcome on next launch. */
    static void dispatch(Context ctx, JSObject event, boolean terminal) {
        if (terminal) {
            try {
                prefs(ctx).edit()
                    .putString(event.getString("eventId"), event.toString())
                    .apply();
            } catch (Exception ignored) { }
        }
        ParallelUploaderPlugin p = instance;
        if (p != null) {
            try { p.notifyListeners("events", event); } catch (Exception ignored) { }
        }
    }

    @PluginMethod
    public void startUpload(PluginCall call) {
        try {
            String filePath = call.getString("filePath");
            JSArray urls = call.getArray("urls");
            String completeUrl = call.getString("completeUrl");
            if (filePath == null || urls == null || urls.length() == 0 || completeUrl == null) {
                call.reject("filePath, urls and completeUrl are required");
                return;
            }
            String id = UUID.randomUUID().toString();
            JSONObject cfg = new JSONObject();
            cfg.put("id", id);
            cfg.put("filePath", filePath);
            cfg.put("urls", urls);
            cfg.put("partSize", (long) call.getDouble("partSize", 8388608.0).doubleValue());
            cfg.put("size", (long) call.getDouble("size", 0.0).doubleValue());
            cfg.put("completeUrl", completeUrl);
            cfg.put("abortUrl", call.getString("abortUrl", ""));
            cfg.put("callbackUrl", call.getString("callbackUrl", ""));
            cfg.put("concurrency", call.getInt("concurrency", 5));
            cfg.put("notificationTitle", call.getString("notificationTitle", "Uploading video"));

            Intent intent = new Intent(getContext(), ParallelUploadService.class);
            intent.setAction(ParallelUploadService.ACTION_START);
            intent.putExtra(ParallelUploadService.EXTRA_CONFIG, cfg.toString());
            ContextCompat.startForegroundService(getContext(), intent);

            JSObject ret = new JSObject();
            ret.put("id", id);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("startUpload failed: " + e.getMessage());
        }
    }

    @PluginMethod
    public void removeUpload(PluginCall call) {
        String id = call.getString("id");
        if (id == null) { call.reject("id is required"); return; }
        Intent intent = new Intent(getContext(), ParallelUploadService.class);
        intent.setAction(ParallelUploadService.ACTION_CANCEL);
        intent.putExtra(ParallelUploadService.EXTRA_ID, id);
        // If the service is not running this starts it just to no-op+stop,
        // which is harmless and simpler than tracking liveness here.
        getContext().startService(intent);
        call.resolve();
    }

    @PluginMethod
    public void acknowledgeEvent(PluginCall call) {
        String eventId = call.getString("eventId");
        if (eventId != null) prefs(getContext()).edit().remove(eventId).apply();
        call.resolve();
    }
}
