package dogido.fabric.protocol;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dogido.fabric.DogidoBuildInfo;
import dogido.fabric.protocol.WorkshopDisplayPayload;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;
import net.minecraft.client.MinecraftClient;
import org.slf4j.Logger;

public final class DogidoEventClient {
    public static final class ConnectionSettings {
        private final boolean enabled;
        private final String serverBaseUrl;
        private final String authToken;

        public ConnectionSettings(boolean enabled, String serverBaseUrl, String authToken) {
            this.enabled = enabled;
            this.serverBaseUrl = serverBaseUrl;
            this.authToken = authToken;
        }

        private String eventEndpoint() {
            return this.serverBaseUrl + "/api/v1/game-events";
        }

        private String sessionEndpoint() {
            return this.serverBaseUrl + "/api/v1/adapter-sessions";
        }

        private boolean hasAuthToken() {
            return !this.authToken.isBlank();
        }
    }

    private final Logger logger;
    private final ConnectionSettings config;
    private final HttpClient httpClient;
    private final Gson gson;
    private final AtomicLong sequence;
    private final DogidoCommandProtocol.State commandState;
    private final Consumer<DogidoCommandProtocol.SelectHotbarCommand> commandHandler;
    private volatile String sessionId;
    private java.util.UUID lastPlayerUuid;
    private boolean bodySupported;
    private volatile String lastPlayerName = "unknown";
    private final AtomicBoolean displayRequestInFlight = new AtomicBoolean();
    private final AtomicLong worldEpoch = new AtomicLong();
    private final AtomicBoolean registrationInFlight = new AtomicBoolean();
    private volatile long nextRegistrationNanos;
    private volatile Boolean preferredVoiceEnabled;

    public record SessionRef(String sessionId, long worldEpoch) {}

    public synchronized SessionRef currentSession() {
        return this.config.enabled && this.sessionId != null
            ? new SessionRef(this.sessionId, this.worldEpoch.get()) : null;
    }

    public synchronized boolean isCurrent(SessionRef ref) {
        return ref != null && ref.worldEpoch() == this.worldEpoch.get()
            && ref.sessionId().equals(this.sessionId);
    }

    public void rememberVoicePreference(Boolean enabled) { this.preferredVoiceEnabled = enabled; }

    CompletableFuture<HttpResponse<String>> dialogueRequest(SessionRef ref, String path, JsonObject payload) {
        if (!isCurrent(ref)) return CompletableFuture.failedFuture(new IllegalStateException("stale_session"));
        try {
            HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(this.config.serverBaseUrl + path))
                .timeout(Duration.ofSeconds(3));
            if (payload == null) request.GET();
            else request.header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(this.gson.toJson(payload)));
            if (this.config.hasAuthToken()) request.header("Authorization", "Bearer " + this.config.authToken);
            return this.httpClient.sendAsync(request.build(), HttpResponse.BodyHandlers.ofString())
                .thenApply(response -> {
                    if (!isCurrent(ref)) throw new IllegalStateException("stale_session");
                    if (DogidoCommandProtocol.isUnknownSessionResponse(response.statusCode(), response.body())) {
                        recoverUnknownSession(ref.sessionId());
                        throw new IllegalStateException("stale_session");
                    }
                    return response;
                });
        } catch (RuntimeException error) {
            return CompletableFuture.failedFuture(error);
        }
    }

    public DogidoEventClient(
        Logger logger,
        ConnectionSettings config,
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

    public long nextSequence() {
        return this.sequence.incrementAndGet();
    }

    public long currentSequence() { return this.sequence.get(); }

    public synchronized void invalidateDisplayRequests() { this.worldEpoch.incrementAndGet(); }

    public synchronized void disconnected() {
        this.sessionId = null;
        this.nextRegistrationNanos = 0;
        invalidateDisplayRequests();
    }

    public void pollWorkshopDisplay(Consumer<WorkshopDisplayPayload.Snapshot> handler) {
        SessionRef ref = currentSession();
        if (ref == null || !this.displayRequestInFlight.compareAndSet(false, true)) return;
        String requestSession = ref.sessionId();
        try {
            HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(this.config.serverBaseUrl
                + "/api/v1/haiku-workshop/snapshot?session_id="
                + java.net.URLEncoder.encode(requestSession, java.nio.charset.StandardCharsets.UTF_8)))
                .timeout(Duration.ofSeconds(3)).GET();
            if (this.config.hasAuthToken()) request.header("Authorization", "Bearer " + this.config.authToken);
            this.httpClient.sendAsync(request.build(), HttpResponse.BodyHandlers.ofString())
                .whenComplete((response, error) -> {
                    WorkshopDisplayPayload.Snapshot snapshot = error == null && response.statusCode() == 200
                        ? WorkshopDisplayPayload.parse(response.body(), requestSession) : null;
                    MinecraftClient.getInstance().execute(() -> {
                        // Check again on the render/game thread, after any leave/rejoin or recovery.
                        if (isCurrent(ref)) handler.accept(snapshot);
                    });
                    this.displayRequestInFlight.set(false);
                });
        } catch (RuntimeException error) {
            this.displayRequestInFlight.set(false);
            handler.accept(null);
        }
    }

    /** UUIDは登録時に固定する身体owner照合用。表示名のoverrideとは別に実playerから受け取る。 */
    public synchronized void ensureSession(String playerName, java.util.UUID playerUuid, boolean bodySupported) {
        this.lastPlayerUuid = playerUuid;
        this.bodySupported = bodySupported;
        ensureSession(playerName);
    }

    public synchronized void ensureSession(String playerName) {
        if (playerName != null && !playerName.isBlank()) {
            this.lastPlayerName = playerName;
        }
        if (this.sessionId != null || !this.config.enabled
                || System.nanoTime() < this.nextRegistrationNanos
                || !this.registrationInFlight.compareAndSet(false, true)) {
            return;
        }
        long registrationEpoch = this.worldEpoch.get();
        this.nextRegistrationNanos = System.nanoTime() + Duration.ofSeconds(5).toNanos();

        JsonObject payload = new JsonObject();
        payload.addProperty("adapter_name", DogidoBuildInfo.ADAPTER_NAME);
        payload.addProperty("adapter_version", DogidoBuildInfo.ADAPTER_VERSION);
        payload.addProperty("game", DogidoBuildInfo.GAME);
        payload.addProperty("schema_version", DogidoBuildInfo.SCHEMA_VERSION);
        payload.addProperty("player_name", playerName);
        if (this.lastPlayerUuid != null) payload.addProperty("player_uuid", this.lastPlayerUuid.toString());
        payload.addProperty("profile_name", DogidoBuildInfo.PROFILE_NAME);
        payload.addProperty("adapter_build", DogidoBuildInfo.ADAPTER_BUILD);
        if (this.preferredVoiceEnabled != null) payload.addProperty("voice_enabled", this.preferredVoiceEnabled);
        JsonArray capabilities = new JsonArray();
        capabilities.add("player_state");
        if (this.bodySupported) capabilities.add("body_observation.v1");
        capabilities.add("inventory");
        capabilities.add("hotbar_slots");
        capabilities.add("visual_threats");
        capabilities.add("auditory_threats");
        capabilities.add("smell_observation");
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
        capabilities.add("dialogue_text.v1");
        payload.add("capabilities", capabilities);
        JsonArray executionCapabilities = new JsonArray();
        executionCapabilities.add("client.hotbar.select.v1");
        payload.add("execution_capabilities", executionCapabilities);

        try {
            HttpRequest.Builder requestBuilder = HttpRequest.newBuilder(URI.create(this.config.sessionEndpoint()))
                .timeout(Duration.ofSeconds(5))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(this.gson.toJson(payload)));
            if (this.config.hasAuthToken()) {
                requestBuilder.header("Authorization", "Bearer " + this.config.authToken);
            }
            this.httpClient.sendAsync(requestBuilder.build(), HttpResponse.BodyHandlers.ofString())
                .whenComplete((response, error) -> {
                    try {
                        synchronized (this) {
                            if (registrationEpoch != this.worldEpoch.get()) return;
                            if (error != null || response.statusCode() / 100 != 2) {
                                this.logger.warn("Dogido session create failed: {}",
                                    error != null ? error.getMessage() : response.statusCode());
                                return;
                            }
                            JsonObject body = JsonParser.parseString(response.body()).getAsJsonObject();
                            String created = body.get("session_id").getAsString();
                            if (!created.isBlank()) {
                                this.sessionId = created;
                                this.logger.info("Dogido session created: {}", this.sessionId);
                            }
                        }
                    } catch (RuntimeException invalid) {
                        this.logger.warn("Dogido session response invalid: {}", invalid.getMessage());
                    } finally {
                        this.registrationInFlight.set(false);
                    }
                });
        } catch (RuntimeException error) {
            this.registrationInFlight.set(false);
            this.logger.warn("Dogido session create failed: {}", error.getMessage());
        }
    }

    /**
     * 観測の写しへ未ACKの操作結果を付け、非同期で本体へ送る。
     * 送信時のsession・worldEpochが変わった応答は捨て、退出前の命令を新接続へ持ち込まない。
     * unknown_sessionでは再登録するが、古い観測は再送しない。受理応答のACKで操作結果を外し、
     * 新規の型付き命令だけをhandlerへ渡す。期限・現在slot・期待itemの検査と実行はadapter側が担う。
     */
    public CompletableFuture<Void> postEvent(JsonObject payload) {
        SessionRef ref = currentSession();
        if (ref == null) return CompletableFuture.completedFuture(null);

        JsonObject outbound = payload.deepCopy();
        outbound.add("command_results", this.commandState.pendingResultsJson());
        HttpRequest.Builder requestBuilder = HttpRequest.newBuilder(URI.create(this.config.eventEndpoint()))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(this.gson.toJson(outbound)));

        String requestSessionId = ref.sessionId();
        requestBuilder.header("X-Dogido-Session-Id", requestSessionId);
        if (this.config.hasAuthToken()) {
            requestBuilder.header("Authorization", "Bearer " + this.config.authToken);
        }

        return this.httpClient.sendAsync(requestBuilder.build(), HttpResponse.BodyHandlers.ofString())
            .thenAccept(response -> {
                if (!isCurrent(ref)) return;
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
            this.nextRegistrationNanos = 0;
            playerName = this.lastPlayerName;
        }
        this.ensureSession(playerName);
    }

    public void rememberCommandResult(JsonObject result) {
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
