package com.heb.pipeline;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.os.Build;
import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
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
}
