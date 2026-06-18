package com.odysseus.simplesignal;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

final class OdysseusNotifications {
    private static final String RESEARCH_CHANNEL_ID = "odysseus_research_complete";
    private static final int RESEARCH_NOTIFICATION_BASE = 7300;

    private OdysseusNotifications() {
    }

    static void showResearchComplete(Context context, String researchId, String query) {
        if (context == null) return;
        Context appContext = context.getApplicationContext();
        if (!canPostNotifications(appContext)) return;

        NotificationManager manager = (NotificationManager) appContext.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        createResearchChannel(manager);

        String safeId = valueOr(researchId, "research");
        String safeQuery = valueOr(query, "").trim();
        String body = safeQuery.isEmpty()
                ? "Your Deep Research report is ready."
                : "Research on \"" + trimForNotification(safeQuery, 72) + "\" is ready.";

        Intent launchIntent = new Intent(appContext, MainActivity.class);
        launchIntent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        launchIntent.putExtra("open_research_id", safeId);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                appContext,
                positiveHash(safeId),
                launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(appContext, RESEARCH_CHANNEL_ID)
                : new Notification.Builder(appContext);

        Notification notification = builder
                .setSmallIcon(R.drawable.ic_stat_odysseus)
                .setContentTitle("Research complete")
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .setOnlyAlertOnce(false)
                .setWhen(System.currentTimeMillis())
                .setShowWhen(true)
                .setCategory(Notification.CATEGORY_STATUS)
                .setPriority(Notification.PRIORITY_DEFAULT)
                .build();

        manager.notify(RESEARCH_NOTIFICATION_BASE + (positiveHash(safeId) % 100000), notification);
    }

    private static boolean canPostNotifications(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true;
        return context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
    }

    private static void createResearchChannel(NotificationManager manager) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel channel = new NotificationChannel(
                RESEARCH_CHANNEL_ID,
                "Deep Research",
                NotificationManager.IMPORTANCE_DEFAULT
        );
        channel.setDescription("Alerts when an Android Deep Research report is ready.");
        manager.createNotificationChannel(channel);
    }

    private static String trimForNotification(String text, int max) {
        String normalized = text.replaceAll("\\s+", " ").trim();
        if (normalized.length() <= max) return normalized;
        return normalized.substring(0, Math.max(1, max - 1)).trim() + "...";
    }

    private static int positiveHash(String value) {
        return valueOr(value, "").hashCode() & 0x7fffffff;
    }

    private static String valueOr(String value, String fallback) {
        return value == null ? fallback : value;
    }
}
