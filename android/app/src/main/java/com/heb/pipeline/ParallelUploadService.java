package com.heb.pipeline;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;
import com.getcapacitor.JSObject;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLongArray;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Foreground service that uploads one file as N concurrent byte-range PUTs to
 * presigned R2 part URLs, then completes the multipart DIRECTLY against R2
 * via the presigned complete URL. That last point is deliberate: once the
 * complete POST lands, the assembled object EXISTS in the bucket, so the
 * backend's 5-minute sweep can land + spawn the job even if the app (and this
 * process) die right after - the web layer's /upload_r2/complete call is just
 * the fast path.
 *
 * Deliberately NOT wired into MainActivity.onDestroy's stopAllUploads (that
 * kills the stock uploader on swipe-away because nothing could spawn the job
 * afterwards) - with the sweep, finishing the upload after a swipe-away is
 * exactly what the user wants: the "processing" push still arrives.
 */
public class ParallelUploadService extends Service {

    static final String ACTION_START = "com.heb.pipeline.parallel.START";
    static final String ACTION_CANCEL = "com.heb.pipeline.parallel.CANCEL";
    static final String EXTRA_CONFIG = "config";
    static final String EXTRA_ID = "id";

    private static final String CHANNEL_ID = "hebpipe_parallel_uploads";
    private static final int NOTIF_ID = 4821;
    private static final int PART_ATTEMPTS = 4;
    private static final int COMPLETE_ATTEMPTS = 3;
    private static final int BUF_SIZE = 64 * 1024;

    private final AtomicBoolean cancelled = new AtomicBoolean(false);
    private volatile ExecutorService pool;
    private volatile String currentId;
    private Thread worker;
    private PowerManager.WakeLock wakeLock;

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "Uploads", NotificationManager.IMPORTANCE_LOW);
            ch.enableVibration(false);
            ch.setSound(null, null);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        if (ACTION_CANCEL.equals(intent.getAction())) {
            String id = intent.getStringExtra(EXTRA_ID);
            if (currentId != null && currentId.equals(id)) {
                cancelled.set(true);
                ExecutorService p = pool;
                if (p != null) p.shutdownNow();
            } else if (worker == null || !worker.isAlive()) {
                stopSelf();
            }
            return START_NOT_STICKY;
        }
        if (worker != null && worker.isAlive()) {
            // One upload at a time - the app never starts a second one while
            // the first runs (upload UI is modal), so just ignore.
            return START_NOT_STICKY;
        }
        final JSONObject cfg;
        try {
            cfg = new JSONObject(intent.getStringExtra(EXTRA_CONFIG));
        } catch (Exception e) { stopSelf(); return START_NOT_STICKY; }
        currentId = cfg.optString("id");
        cancelled.set(false);

        startAsForeground(cfg.optString("notificationTitle", "Uploading video"), 0);
        worker = new Thread(() -> run(cfg), "hebpipe-parallel-upload");
        worker.start();
        return START_NOT_STICKY;
    }

    private void startAsForeground(String title, int pct) {
        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
            ? ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC : 0;
        ServiceCompat.startForeground(this, NOTIF_ID, buildNotification(title, pct), type);
    }

    private android.app.Notification buildNotification(String title, int pct) {
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, pct, false)
            // Android 12+ DELAYS a foreground service's notification by 10 s so
            // short-lived services can finish unseen. The parallel R2 path
            // lands a typical clip in a few seconds, so the progress bar was
            // being cancelled before it was ever shown ("nothing appears until
            // the video is ready", Android 16 field report 2026-08-22). Opt out:
            // show it the moment the service goes foreground.
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build();
    }

    private void run(JSONObject cfg) {
        final String id = cfg.optString("id");
        final String filePath = cfg.optString("filePath");
        final String completeUrl = cfg.optString("completeUrl");
        final String abortUrl = cfg.optString("abortUrl", "");
        final long partSize = cfg.optLong("partSize", 8L * 1024 * 1024);
        final int concurrency = Math.max(1, Math.min(8, cfg.optInt("concurrency", 5)));
        final String title = cfg.optString("notificationTitle", "Uploading video");

        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hebpipe:parallelUpload");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire(60 * 60 * 1000L);   // hard cap, released in finally
        }
        Thread progress = null;
        try {
            JSONArray urlsArr = cfg.optJSONArray("urls");
            final int nParts = urlsArr.length();
            final String[] urls = new String[nParts];
            for (int i = 0; i < nParts; i++) urls[i] = urlsArr.getString(i);

            long size = cfg.optLong("size", 0);
            if (size <= 0) size = probeSize(filePath);
            if (size <= 0) throw new IllegalStateException("file size unknown");
            final long totalSize = size;

            final AtomicLongArray sent = new AtomicLongArray(nParts);
            final String[] etags = new String[nParts];

            // Progress: notification + JS event, throttled to ~1/s.
            progress = new Thread(() -> {
                NotificationManager nm = getSystemService(NotificationManager.class);
                int lastPct = -1;
                while (!Thread.currentThread().isInterrupted()) {
                    long done = 0;
                    for (int i = 0; i < nParts; i++) done += sent.get(i);
                    int pct = (int) Math.min(99, done * 100 / totalSize);
                    if (pct != lastPct) {
                        lastPct = pct;
                        if (nm != null) nm.notify(NOTIF_ID, buildNotification(title, pct));
                        JSObject ev = event(id, "uploading");
                        JSObject payload = new JSObject();
                        payload.put("percent", pct);
                        ev.put("payload", payload);
                        ParallelUploaderPlugin.dispatch(this, ev, false);
                    }
                    try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
                }
            }, "hebpipe-upload-progress");
            progress.setDaemon(true);
            progress.start();

            pool = Executors.newFixedThreadPool(concurrency);
            List<Future<?>> futures = new ArrayList<>();
            for (int p = 0; p < nParts; p++) {
                final int part = p;
                futures.add(pool.submit(() -> {
                    long start = part * partSize;
                    long len = Math.min(partSize, totalSize - start);
                    etags[part] = putPart(urls[part], filePath, start, len, part, sent);
                    return null;
                }));
            }
            for (Future<?> f : futures) f.get();   // rethrows the first failure
            pool.shutdown();
            progress.interrupt();

            if (cancelled.get()) throw new InterruptedException("cancelled");

            completeMultipart(completeUrl, etags);

            JSObject ev = event(id, "completed");
            JSObject payload = new JSObject();
            payload.put("statusCode", 200);
            ev.put("payload", payload);
            ParallelUploaderPlugin.dispatch(this, ev, true);
        } catch (Throwable t) {
            boolean wasCancelled = cancelled.get()
                || t instanceof InterruptedException
                || t.getCause() instanceof InterruptedException;
            tryAbort(abortUrl);
            JSObject ev = event(id, wasCancelled ? "cancelled" : "failed");
            JSObject payload = new JSObject();
            if (!wasCancelled) {
                String msg = t.getMessage();
                if (msg == null && t.getCause() != null) msg = t.getCause().getMessage();
                payload.put("error", msg != null ? msg : t.getClass().getSimpleName());
            }
            ev.put("payload", payload);
            ParallelUploaderPlugin.dispatch(this, ev, true);
        } finally {
            // Must happen here, not only on the success path above: a part
            // that fails all its attempts makes f.get() rethrow, which used to
            // skip that interrupt and leak this thread (and its Service
            // reference) for the life of the process. Interrupting twice on
            // the success path is harmless.
            if (progress != null) progress.interrupt();
            ExecutorService p = pool;
            if (p != null) p.shutdownNow();
            if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE);
            stopSelf();
        }
    }

    private JSObject event(String id, String name) {
        JSObject ev = new JSObject();
        ev.put("name", name);
        ev.put("id", id);
        ev.put("eventId", UUID.randomUUID().toString());
        return ev;
    }

    private long probeSize(String filePath) {
        try {
            if (filePath.startsWith("content:")) {
                android.content.res.AssetFileDescriptor fd = getContentResolver()
                    .openAssetFileDescriptor(Uri.parse(filePath), "r");
                if (fd != null) { long l = fd.getLength(); fd.close(); return l; }
                return -1;
            }
            return new File(filePath.startsWith("file://") ? filePath.substring(7) : filePath).length();
        } catch (Exception e) { return -1; }
    }

    private InputStream openAt(String filePath, long offset) throws Exception {
        InputStream in;
        if (filePath.startsWith("content:")) {
            in = getContentResolver().openInputStream(Uri.parse(filePath));
        } else {
            in = new FileInputStream(filePath.startsWith("file://") ? filePath.substring(7) : filePath);
        }
        long remaining = offset;
        while (remaining > 0) {
            long skipped = in.skip(remaining);
            if (skipped <= 0) {
                // Some content streams refuse to skip - fall back to reading.
                byte[] scratch = new byte[(int) Math.min(remaining, BUF_SIZE)];
                int read = in.read(scratch);
                if (read <= 0) { in.close(); throw new IllegalStateException("could not seek to part offset"); }
                skipped = read;
            }
            remaining -= skipped;
        }
        return in;
    }

    private String putPart(String url, String filePath, long start, long len,
                           int part, AtomicLongArray sent) throws Exception {
        Exception last = null;
        for (int attempt = 0; attempt < PART_ATTEMPTS; attempt++) {
            if (cancelled.get() || Thread.currentThread().isInterrupted())
                throw new InterruptedException("cancelled");
            if (attempt > 0) Thread.sleep(2000L * attempt);
            sent.set(part, 0);
            HttpURLConnection conn = null;
            InputStream in = null;
            try {
                conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setRequestMethod("PUT");
                conn.setDoOutput(true);
                conn.setFixedLengthStreamingMode(len);
                conn.setConnectTimeout(30_000);
                conn.setReadTimeout(120_000);
                in = openAt(filePath, start);
                OutputStream out = conn.getOutputStream();
                byte[] buf = new byte[BUF_SIZE];
                long remaining = len;
                while (remaining > 0) {
                    if (cancelled.get()) throw new InterruptedException("cancelled");
                    int n = in.read(buf, 0, (int) Math.min(buf.length, remaining));
                    if (n < 0) throw new IllegalStateException("file truncated mid-part");
                    out.write(buf, 0, n);
                    remaining -= n;
                    sent.addAndGet(part, n);
                }
                out.close();
                int code = conn.getResponseCode();
                String etag = conn.getHeaderField("ETag");
                if (code >= 200 && code < 300 && etag != null) return etag;
                last = new IllegalStateException("part " + part + " HTTP " + code);
            } catch (InterruptedException e) {
                throw e;
            } catch (Exception e) {
                last = e;
            } finally {
                if (in != null) try { in.close(); } catch (Exception ignored) { }
                if (conn != null) conn.disconnect();
            }
        }
        throw last != null ? last : new IllegalStateException("part " + part + " failed");
    }

    private void completeMultipart(String completeUrl, String[] etags) throws Exception {
        StringBuilder xml = new StringBuilder("<CompleteMultipartUpload>");
        for (int i = 0; i < etags.length; i++) {
            xml.append("<Part><PartNumber>").append(i + 1).append("</PartNumber>")
               .append("<ETag>").append(etags[i]).append("</ETag></Part>");
        }
        xml.append("</CompleteMultipartUpload>");
        byte[] body = xml.toString().getBytes("UTF-8");

        Exception last = null;
        for (int attempt = 0; attempt < COMPLETE_ATTEMPTS; attempt++) {
            if (attempt > 0) Thread.sleep(2000L * attempt);
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(completeUrl).openConnection();
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setFixedLengthStreamingMode(body.length);
                conn.setRequestProperty("Content-Type", "application/xml");
                conn.setConnectTimeout(30_000);
                conn.setReadTimeout(120_000);
                OutputStream out = conn.getOutputStream();
                out.write(body);
                out.close();
                int code = conn.getResponseCode();
                // S3 quirk: CompleteMultipartUpload can answer 200 with an
                // <Error> document in the body - success must be verified.
                String resp = readBody(conn);
                if (code >= 200 && code < 300 && !resp.contains("<Error>")) return;
                last = new IllegalStateException("complete HTTP " + code + ": "
                    + resp.substring(0, Math.min(200, resp.length())));
            } catch (Exception e) {
                last = e;
            } finally {
                if (conn != null) conn.disconnect();
            }
        }
        throw last != null ? last : new IllegalStateException("complete failed");
    }

    private String readBody(HttpURLConnection conn) {
        try {
            InputStream s = conn.getResponseCode() < 400
                ? conn.getInputStream() : conn.getErrorStream();
            if (s == null) return "";
            BufferedReader r = new BufferedReader(new InputStreamReader(s));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null && sb.length() < 4096) sb.append(line);
            r.close();
            return sb.toString();
        } catch (Exception e) { return ""; }
    }

    private void tryAbort(String abortUrl) {
        if (abortUrl == null || abortUrl.isEmpty()) return;
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(abortUrl).openConnection();
            conn.setRequestMethod("DELETE");
            conn.setConnectTimeout(15_000);
            conn.setReadTimeout(30_000);
            conn.getResponseCode();
        } catch (Exception ignored) {
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
