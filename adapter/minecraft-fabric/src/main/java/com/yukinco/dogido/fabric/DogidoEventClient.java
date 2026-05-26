package com.yukinco.dogido.fabric;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicLong;

import org.slf4j.Logger;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

final class DogidoEventClient {
    private static final String SCHEMA_VERSION = "2026-05-24";

    private final Logger logger;
    private final DogidoConfig config;
    private final HttpClient httpClient;
    private final Gson gson;
    private final AtomicLong sequence;
    private volatile String sessionId;

    DogidoEventClient(Logger logger, DogidoConfig config) {
        this.logger = logger;
        this.config = config;
        this.gson = new Gson();
        this.sequence = new AtomicLong();
        this.httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(3))
            .build();
    }

    long nextSequence() {
        return this.sequence.incrementAndGet();
    }

    void ensureSession(String playerName) {
        if (this.sessionId != null || !this.config.enabled) {
            return;
        }

        JsonObject payload = new JsonObject();
        payload.addProperty("adapter_name", "dogido-fabric-client");
        payload.addProperty("adapter_version", "0.1.0");
        payload.addProperty("game", "minecraft-java");
        payload.addProperty("schema_version", SCHEMA_VERSION);
        payload.addProperty("player_name", playerName);
        payload.addProperty("profile_name", "default");
        JsonArray capabilities = new JsonArray();
        capabilities.add("player_state");
        capabilities.add("inventory");
        capabilities.add("visual_threats");
        capabilities.add("auditory_threats");
        capabilities.add("danger_darkness");
        capabilities.add("combat_state");
        capabilities.add("death_events");
        payload.add("capabilities", capabilities);

        HttpRequest.Builder requestBuilder = HttpRequest.newBuilder(URI.create(this.config.sessionEndpoint()))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(this.gson.toJson(payload)));

        if (this.config.hasAuthToken()) {
            requestBuilder.header("Authorization", "Bearer " + this.config.authToken);
        }

        try {
            HttpResponse<String> response = this.httpClient.send(
                requestBuilder.build(),
                HttpResponse.BodyHandlers.ofString()
            );
            if (response.statusCode() / 100 != 2) {
                this.logger.warn(
                    "Dogido session create failed: status={} body={}",
                    response.statusCode(),
                    response.body()
                );
                return;
            }
            JsonObject body = JsonParser.parseString(response.body()).getAsJsonObject();
            if (body.has("session_id")) {
                this.sessionId = body.get("session_id").getAsString();
                this.logger.info("Dogido session created: {}", this.sessionId);
            }
        } catch (IOException | InterruptedException e) {
            this.logger.warn("Dogido session create failed: {}", e.getMessage());
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
        }
    }

    CompletableFuture<Void> postEvent(JsonObject payload) {
        if (!this.config.enabled) {
            return CompletableFuture.completedFuture(null);
        }

        HttpRequest.Builder requestBuilder = HttpRequest.newBuilder(URI.create(this.config.eventEndpoint()))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(this.gson.toJson(payload)));

        if (this.sessionId != null) {
            requestBuilder.header("X-Dogido-Session-Id", this.sessionId);
        }
        if (this.config.hasAuthToken()) {
            requestBuilder.header("Authorization", "Bearer " + this.config.authToken);
        }

        return this.httpClient.sendAsync(requestBuilder.build(), HttpResponse.BodyHandlers.ofString())
            .thenAccept(response -> {
                if (response.statusCode() / 100 != 2) {
                    this.logger.warn(
                        "Dogido event rejected: status={} body={}",
                        response.statusCode(),
                        response.body()
                    );
                }
            })
            .exceptionally(error -> {
                this.logger.warn("Dogido event send failed: {}", error.getMessage());
                return null;
            });
    }
}
