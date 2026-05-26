package com.yukinco.dogido.fabric;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

import net.fabricmc.loader.api.FabricLoader;

final class DogidoConfig {
    final boolean enabled;
    final String serverBaseUrl;
    final String authToken;
    final int snapshotIntervalTicks;
    final int threatScanIntervalTicks;
    final int audioScanIntervalTicks;
    final int combatEndedQuietTicks;
    final double maxThreatDistance;
    final double visibleThreatDistance;
    final double audioThreatDistance;
    final double panicDistance;
    final double rearWarningDistance;
    final String playerNameOverride;

    private DogidoConfig(
        boolean enabled,
        String serverBaseUrl,
        String authToken,
        int snapshotIntervalTicks,
        int threatScanIntervalTicks,
        int audioScanIntervalTicks,
        int combatEndedQuietTicks,
        double maxThreatDistance,
        double visibleThreatDistance,
        double audioThreatDistance,
        double panicDistance,
        double rearWarningDistance,
        String playerNameOverride
    ) {
        this.enabled = enabled;
        this.serverBaseUrl = serverBaseUrl;
        this.authToken = authToken;
        this.snapshotIntervalTicks = snapshotIntervalTicks;
        this.threatScanIntervalTicks = threatScanIntervalTicks;
        this.audioScanIntervalTicks = audioScanIntervalTicks;
        this.combatEndedQuietTicks = combatEndedQuietTicks;
        this.maxThreatDistance = maxThreatDistance;
        this.visibleThreatDistance = visibleThreatDistance;
        this.audioThreatDistance = audioThreatDistance;
        this.panicDistance = panicDistance;
        this.rearWarningDistance = rearWarningDistance;
        this.playerNameOverride = playerNameOverride;
    }

    static DogidoConfig load() {
        Path configPath = FabricLoader.getInstance()
            .getConfigDir()
            .resolve("dogido-fabric-client.properties");
        Properties defaults = createDefaults();
        Properties properties = new Properties();
        properties.putAll(defaults);

        if (Files.exists(configPath)) {
            try (InputStream input = Files.newInputStream(configPath)) {
                properties.load(input);
            } catch (IOException ignored) {
                // Keep defaults and overwrite on next save.
            }
        } else {
            writeDefaults(configPath, defaults);
        }

        return new DogidoConfig(
            Boolean.parseBoolean(properties.getProperty("enabled", "true")),
            properties.getProperty("server_base_url", "http://127.0.0.1:5055"),
            properties.getProperty("auth_token", "").trim(),
            Integer.parseInt(properties.getProperty("snapshot_interval_ticks", "20")),
            Integer.parseInt(properties.getProperty("threat_scan_interval_ticks", "4")),
            Integer.parseInt(properties.getProperty("audio_scan_interval_ticks", "8")),
            Integer.parseInt(properties.getProperty("combat_ended_quiet_ticks", "100")),
            Double.parseDouble(properties.getProperty("max_threat_distance", "16.0")),
            Double.parseDouble(properties.getProperty("visible_threat_distance", "24.0")),
            Double.parseDouble(properties.getProperty("audio_threat_distance", "12.0")),
            Double.parseDouble(properties.getProperty("panic_distance", "7.0")),
            Double.parseDouble(properties.getProperty("rear_warning_distance", "8.0")),
            properties.getProperty("player_name_override", "").trim()
        );
    }

    String eventEndpoint() {
        return this.serverBaseUrl + "/api/v1/game-events";
    }

    String sessionEndpoint() {
        return this.serverBaseUrl + "/api/v1/adapter-sessions";
    }

    boolean hasAuthToken() {
        return !this.authToken.isBlank();
    }

    private static Properties createDefaults() {
        Properties properties = new Properties();
        properties.setProperty("enabled", "true");
        properties.setProperty("server_base_url", "http://127.0.0.1:5055");
        properties.setProperty("auth_token", "");
        properties.setProperty("snapshot_interval_ticks", "20");
        properties.setProperty("threat_scan_interval_ticks", "4");
        properties.setProperty("audio_scan_interval_ticks", "8");
        properties.setProperty("combat_ended_quiet_ticks", "100");
        properties.setProperty("max_threat_distance", "16.0");
        properties.setProperty("visible_threat_distance", "24.0");
        properties.setProperty("audio_threat_distance", "12.0");
        properties.setProperty("panic_distance", "7.0");
        properties.setProperty("rear_warning_distance", "8.0");
        properties.setProperty("player_name_override", "");
        return properties;
    }

    private static void writeDefaults(Path configPath, Properties defaults) {
        try {
            Files.createDirectories(configPath.getParent());
            try (OutputStream output = Files.newOutputStream(configPath)) {
                defaults.store(output, "Dogido Fabric Client");
            }
        } catch (IOException ignored) {
            // Leave it in-memory only.
        }
    }
}
