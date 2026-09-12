package dogido.fabric;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import net.minecraft.client.MinecraftClient;

import org.slf4j.Logger;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

final class DogidoEventClient {
    private final Logger logger;
    private final DogidoConfig config;
    private final HttpClient httpClient;
    private final Gson gson;
    private final AtomicLong sequence;
    private final DogidoCommandProtocol.State commandState;
    private final Consumer<DogidoCommandProtocol.SelectHotbarCommand> commandHandler;
    private volatile String sessionId;
    private volatile String lastPlayerName = "unknown";
    private final AtomicBoolean displayRequestInFlight = new AtomicBoolean();
    private final AtomicLong worldEpoch = new AtomicLong();

    DogidoEventClient(
        Logger logger,
        DogidoConfig config,
        Consumer<DogidoCommandProtocol.SelectHotbarCommand> commandHandler
    ) {
        this.logger = logger;
        this.config = config;
        this.commandHandler = commandHandler;
        this.gson = new Gson();
        this.sequence = new AtomicLong();
        this.commandState = new DogidoCommandProtocol.State();
        this.httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(3))
            .build();
    }

    long nextSequence() {
        return this.sequence.incrementAndGet();
    }

    long currentSequence() { return this.sequence.get(); }

    void invalidateDisplayRequests() { this.worldEpoch.incrementAndGet(); }

    void disconnected() {
        this.sessionId = null;
        invalidateDisplayRequests();
    }

    void pollWorkshopDisplay(Consumer<WorkshopDisplayState.Snapshot> handler) {
        String requestSession = this.sessionId;
        long epoch = this.worldEpoch.get();
        if (!this.config.enabled || requestSession == null || !this.displayRequestInFlight.compareAndSet(false, true)) return;
        try {
            HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(this.config.serverBaseUrl
                + "/api/v1/haiku-workshop/snapshot?session_id="
                + java.net.URLEncoder.encode(requestSession, java.nio.charset.StandardCharsets.UTF_8)))
                .timeout(Duration.ofSeconds(3)).GET();
            if (this.config.hasAuthToken()) request.header("Authorization", "Bearer " + this.config.authToken);
            this.httpClient.sendAsync(request.build(), HttpResponse.BodyHandlers.ofString())
                .whenComplete((response, error) -> {
                    WorkshopDisplayState.Snapshot snapshot = error == null && response.statusCode() == 200
                        ? WorkshopDisplayState.parse(response.body(), requestSession) : null;
                    MinecraftClient.getInstance().execute(() -> {
                        // Check again on the render/game thread, after any leave/rejoin or recovery.
                        if (epoch == this.worldEpoch.get() && requestSession.equals(this.sessionId)) handler.accept(snapshot);
                    });
                    this.displayRequestInFlight.set(false);
                });
        } catch (RuntimeException error) {
            this.displayRequestInFlight.set(false);
            handler.accept(null);
        }
    }

    synchronized void ensureSession(String playerName) {
        if (playerName != null && !playerName.isBlank()) {
            this.lastPlayerName = playerName;
        }
        if (this.sessionId != null || !this.config.enabled) {
            return;
        }
        long registrationEpoch = this.worldEpoch.get();

        JsonObject payload = new JsonObject();
        payload.addProperty("adapter_name", DogidoBuildInfo.ADAPTER_NAME);
        payload.addProperty("adapter_version", DogidoBuildInfo.ADAPTER_VERSION);
        payload.addProperty("game", DogidoBuildInfo.GAME);
        payload.addProperty("schema_version", DogidoBuildInfo.SCHEMA_VERSION);
        payload.addProperty("player_name", playerName);
        payload.addProperty("profile_name", DogidoBuildInfo.PROFILE_NAME);
        payload.addProperty("adapter_build", DogidoBuildInfo.ADAPTER_BUILD);
        JsonArray capabilities = new JsonArray();
        capabilities.add("player_state");
        capabilities.add("inventory");
        capabilities.add("hotbar_slots");
        capabilities.add("visual_threats");
        capabilities.add("auditory_threats");
        capabilities.add("smell_observation");
        capabilities.add("zombie_scent_clues");
        capabilities.add("ambient_sounds");
        capabilities.add("played_world_sounds");
        capabilities.add("danger_darkness");
        capabilities.add("combat_state");
        capabilities.add("death_events");
        capabilities.add("hostile_outcomes");
        capabilities.add("hostile_defeated_events");
        capabilities.add("hostile_scan_range");
        capabilities.add("absolute_hostile_direction");
        capabilities.add("creeper_fuse_state");
        capabilities.add("creeper_detonation_events");
        capabilities.add("workshop_display.v1");
        payload.add("capabilities", capabilities);
        JsonArray executionCapabilities = new JsonArray();
        executionCapabilities.add("client.hotbar.select.v1");
        payload.add("execution_capabilities", executionCapabilities);

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
            if (body.has("session_id") && registrationEpoch == this.worldEpoch.get()) {
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

        JsonObject outbound = payload.deepCopy();
        outbound.add("command_results", this.commandState.pendingResultsJson());
        HttpRequest.Builder requestBuilder = HttpRequest.newBuilder(URI.create(this.config.eventEndpoint()))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(this.gson.toJson(outbound)));

        String requestSessionId = this.sessionId;
        long requestWorldEpoch = this.worldEpoch.get();
        if (requestSessionId != null) {
            requestBuilder.header("X-Dogido-Session-Id", requestSessionId);
        }
        if (this.config.hasAuthToken()) {
            requestBuilder.header("Authorization", "Bearer " + this.config.authToken);
        }

        return this.httpClient.sendAsync(requestBuilder.build(), HttpResponse.BodyHandlers.ofString())
            .thenAccept(response -> {
                if (requestWorldEpoch != this.worldEpoch.get()
                        || !java.util.Objects.equals(requestSessionId, this.sessionId)) return;
                if (
                    DogidoCommandProtocol.isUnknownSessionResponse(
                        response.statusCode(),
                        response.body()
                    )
                ) {
                    this.recoverUnknownSession(requestSessionId);
                    return;
                }
                if (response.statusCode() / 100 != 2) {
                    this.logger.warn(
                        "Dogido event rejected: status={} body={}",
                        response.statusCode(),
                        response.body()
                    );
                    return;
                }
                this.handleAcceptedResponse(response.body());
            })
            .exceptionally(error -> {
                this.logger.warn("Dogido event send failed: {}", error.getMessage());
                return null;
            });
    }

    private void recoverUnknownSession(String rejectedSessionId) {
        String playerName;
        synchronized (this) {
            if (rejectedSessionId == null || !rejectedSessionId.equals(this.sessionId)) {
                return;
            }
            this.logger.warn(
                "Dogido session expired on server; re-registering: {}",
                rejectedSessionId
            );
            this.sessionId = null;
            this.worldEpoch.incrementAndGet();
            playerName = this.lastPlayerName;
        }
        this.ensureSession(playerName);
    }

    void rememberCommandResult(JsonObject result) {
        this.commandState.rememberResult(result);
    }

    private void handleAcceptedResponse(String body) {
        this.commandState.acknowledge(
            DogidoCommandProtocol.parseAcknowledgedCommandIds(body)
        );
        for (
            DogidoCommandProtocol.SelectHotbarCommand command
                : DogidoCommandProtocol.parseSelectHotbarCommands(body)
        ) {
            if (!this.commandState.markCommandSeen(command.commandId())) {
                continue;
            }
            try {
                this.commandHandler.accept(command);
            } catch (RuntimeException error) {
                this.logger.warn(
                    "Dogido command dispatch failed: command_id={} detail={}",
                    command.commandId(),
                    error.getMessage()
                );
                this.commandState.rememberResult(
                    DogidoCommandProtocol.commandResult(
                        command,
                        "failed",
                        java.time.Instant.now(),
                        null,
                        null,
                        "dispatch_failed"
                    )
                );
            }
        }
    }
}
