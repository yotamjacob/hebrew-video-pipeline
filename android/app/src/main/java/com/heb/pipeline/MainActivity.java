package com.heb.pipeline;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.os.Build;
import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(NativeDownloaderPlugin.class);
        registerPlugin(NativeBillingPlugin.class);

        // Pre-create the background-uploader's notification channel as LOW
        // (silent, no vibration) BEFORE Capacitor loads the @capgo uploader
        // plugin, which would otherwise create it at IMPORTANCE_DEFAULT and make
        // the phone buzz on every upload-progress update. A channel's importance
        // can't be lowered once created, so we must win the race by creating it
        // first here. The id must match the plugin's channel id exactly.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                "ee.forgr.capacitor.uploader.notification_channel_id",
                "Uploads",
                NotificationManager.IMPORTANCE_LOW);
            ch.enableVibration(false);
            ch.setSound(null, null);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
        super.onCreate(savedInstanceState);
    }

    @Override
    public void onDestroy() {
        // If the user fully closes the app (swipe-away from recents), stop any
        // in-flight background upload: with the WebView gone nothing will spawn
        // processing when it finishes, so it would just burn data + battery and
        // leave a zombie progress notification. isFinishing() distinguishes a
        // real close from config changes (already handled via configChanges) and
        // from system process death (which skips onDestroy entirely and kills
        // the service with the process anyway). Minimizing the app does NOT call
        // onDestroy - background uploads still survive that, by design.
        if (isFinishing()) {
            try {
                // Via reflection: the gotev upload lib is a transitive dep of the
                // @capgo uploader plugin and not on this module's compile
                // classpath. @JvmStatic companion method -> plain static call.
                Class.forName("net.gotev.uploadservice.UploadService")
                     .getMethod("stopAllUploads")
                     .invoke(null);
            } catch (Throwable ignored) { /* uploader lib absent or no active uploads */ }
        }
        super.onDestroy();
    }
}
