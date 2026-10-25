package dogido.fabric.protocol;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dogido.fabric.DogidoBuildInfo;
import dogido.fabric.horse_recall.HorseRecallProtocol;
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
import java.util.function.BiConsumer;
import java.util.function.BooleanSupplier;
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
    private final BiConsumer<HorseRecallProtocol.Command, SessionRef> horseCommandHandler;
    private final BooleanSupplier horseRecallAvailable;
    private final BiConsumer<dogido.fabric.body.BodyInspectProtocol.Command,SessionRef> bodyCommandHandler;
    private final BiConsumer<dogido.fabric.body.BodyRequestProtocol.Command,SessionRef> bodyRequestHandler;
    private long bodyRequestGeneration=-1;
    private final BiConsumer<dogido.fabric.screenshot.ScreenshotProtocol.Command,SessionRef> screenshotHandler;
    private final dogido.fabric.screenshot.ScreenshotProtocol.State screenshotState =
        new dogido.fabric.screenshot.ScreenshotProtocol.State();
    private final BiConsumer<dogido.fabric.screenshot.BodyViewProtocol.Command,SessionRef> bodyViewHandler;
    private final Consumer<String> bodyViewAcknowledged;
    private final dogido.fabric.screenshot.BodyViewProtocol.State bodyViewState=new dogido.fabric.screenshot.BodyViewProtocol.State();
    private final AtomicBoolean freshSnapshotRequested=new AtomicBoolean();
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

    /** 身体命令のqueueだけを既存session変更と同じmonitorで確定する。世界の視認・衝突検査は呼出元で済ませる。 */
    public synchronized String submitBodyInspect(SessionRef ref,dogido.fabric.body.BodyInspectProtocol.Command command,
            dogido.fabric.body.BodyConnection.MoveRequest request,
            java.util.function.Function<dogido.fabric.body.BodyConnection.MoveRequest,String> bodyMoveSender) {
        if (!this.config.enabled || !isCurrent(ref)) return "stale_session";
        String timeError=command.timeError(java.time.Instant.now());
        if (timeError != null) return timeError;
        if (!command.commandId().equals(request.actionId()) || !command.context().equals(request.context())) return "invalid_action";
        if (command.purpose() == dogido.fabric.body.BodyInspectProtocol.Purpose.LOOK_OWNER) return "invalid_action";
        return bodyMoveSender.apply(request);
    }

    /** 自発的な本人注視も移動と同じsession monitorで、期限・対象・用途を照合してqueueへ渡す。 */
    public synchronized String submitBodyInspect(SessionRef ref,dogido.fabric.body.BodyInspectProtocol.Command command,
            dogido.fabric.body.BodyConnection.LookRequest request,
            java.util.function.Function<dogido.fabric.body.BodyConnection.LookRequest,String> sender) {
        if (!this.config.enabled || !isCurrent(ref)) return "stale_session";
        String timeError=command.timeError(java.time.Instant.now());
        if (timeError != null) return timeError;
        if (command.purpose() != dogido.fabric.body.BodyInspectProtocol.Purpose.LOOK_OWNER
                || !command.commandId().equals(request.actionId()) || !command.context().equals(request.context())) return "invalid_action";
        return sender.apply(request);
    }

    public synchronized boolean isCurrentBodyRequest(SessionRef ref,long generation) {
        return this.config.enabled && isCurrent(ref) && generation >= 0 && generation == this.bodyRequestGeneration;
    }
    /** BERTへ変更済み。旧判定へ戻さない。送信直前にも同session・入力世代・実時計を照合する。 */
    public synchronized String submitBodyRequest(SessionRef ref,dogido.fabric.body.BodyRequestProtocol.Command command,
            java.util.function.Supplier<String> sender) {
        if (!this.config.enabled || !isCurrent(ref)) return "stale_session";
        if (command == null || !isCurrentBodyRequest(ref,command.inputGeneration())) return "stale_input_generation";
        String error=command.timeError(java.time.Instant.now());
        return error == null ? sender.get() : error;
    }
    public synchronized void rememberBodyRequestResult(SessionRef ref,JsonObject result) {
        if (!result.has("input_generation") || !isCurrentBodyRequest(ref,result.get("input_generation").getAsLong())) return;
        this.commandState.rememberResult(result);
    }
    private synchronized void observeBodyRequestGeneration(String body,SessionRef ref) {
        if (!isCurrent(ref)) return;
        try {
            var root=JsonParser.parseString(body).getAsJsonObject();
            var value=root.get("body_request_generation");
            if (value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isNumber()
                    && value.getAsString().matches("[0-9]{1,19}")) {
                long generation=value.getAsLong();
                if (generation >= 0) this.bodyRequestGeneration=Math.max(this.bodyRequestGeneration,generation);
            }
        } catch (RuntimeException ignored) { }
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

    /** 現行の各命令handlerをまとめて受け取り、sessionと配送台帳を初期化する。 */
    public DogidoEventClient(Logger logger,ConnectionSettings config,
        Consumer<DogidoCommandProtocol.SelectHotbarCommand> commandHandler,
        BiConsumer<HorseRecallProtocol.Command,SessionRef> horseCommandHandler,BooleanSupplier horseRecallAvailable,
        BiConsumer<dogido.fabric.body.BodyInspectProtocol.Command,SessionRef> bodyCommandHandler,
        BiConsumer<dogido.fabric.screenshot.ScreenshotProtocol.Command,SessionRef> screenshotHandler,
        BiConsumer<dogido.fabric.screenshot.BodyViewProtocol.Command,SessionRef> bodyViewHandler,Consumer<String> bodyViewAcknowledged,
        BiConsumer<dogido.fabric.body.BodyRequestProtocol.Command,SessionRef> bodyRequestHandler) {
        this.bodyRequestHandler=bodyRequestHandler;
        this.bodyViewHandler=bodyViewHandler;
        this.bodyViewAcknowledged=bodyViewAcknowledged;
        this.screenshotHandler = screenshotHandler;
        this.bodyCommandHandler = bodyCommandHandler;
        this.logger = logger;
        this.config = config;
        this.commandHandler = commandHandler;
        this.horseCommandHandler = horseCommandHandler;
        this.horseRecallAvailable = horseRecallAvailable;
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

    public synchronized void invalidateDisplayRequests() {
        this.worldEpoch.incrementAndGet();
        this.bodyRequestGeneration=-1;
        this.bodyViewState.clearResults();this.freshSnapshotRequested.set(false);
    }
    /** 次client tickで通常snapshotを新しく組み立てる合図。古い観測の再送や独立poll経路は作らない。 */
    public boolean consumeFreshSnapshotRequest() { return this.freshSnapshotRequested.getAndSet(false); }
    public synchronized void rememberBodyViewResult(SessionRef ref,JsonObject result) {
        if(!isCurrent(ref))return;
        this.bodyViewState.remember(result);this.freshSnapshotRequested.set(true);
    }
    private synchronized JsonArray bodyViewResults(SessionRef ref) {
        return isCurrent(ref)?this.bodyViewState.results():new JsonArray();
    }
    public synchronized void rememberScreenshotResult(SessionRef ref,JsonObject result) {
        // 撮影開始はworldEpochで検証済み。保存中のportal移動でも元の句へ通知できる。
        if (ref != null && ref.sessionId().equals(this.sessionId)) this.screenshotState.remember(result);
    }
    private synchronized JsonArray screenshotResults(SessionRef ref) {
        return isCurrent(ref) ? this.screenshotState.results() : new JsonArray();
    }

    public synchronized void disconnected() {
        this.sessionId = null;
        this.screenshotState.clearResults();
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
        if (this.bodySupported) {
            capabilities.add("body_observation.v3");
            if (this.bodyCommandHandler != null) capabilities.add("body_autonomy.v2");
        }
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
        if (this.bodySupported && this.screenshotHandler != null) executionCapabilities.add(dogido.fabric.screenshot.ScreenshotProtocol.CAPABILITY);
        if (this.horseRecallAvailable.getAsBoolean()) executionCapabilities.add(HorseRecallProtocol.CAPABILITY);
        if (this.bodySupported && this.bodyCommandHandler != null) executionCapabilities.add(dogido.fabric.body.BodyInspectProtocol.CAPABILITY);
        if (this.bodySupported && this.bodyRequestHandler != null) executionCapabilities.add(dogido.fabric.body.BodyRequestProtocol.CAPABILITY);
        if (this.bodySupported && this.bodyViewHandler!=null) executionCapabilities.add(dogido.fabric.screenshot.BodyViewProtocol.CAPABILITY);
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
        JsonArray results=dogido.fabric.screenshot.ScreenshotProtocol.resultBatch(
            this.commandState.pendingResultsJson(),screenshotResults(ref));
        JsonArray bodyViews=bodyViewResults(ref);
        boolean bodyViewDelivery=!bodyViews.isEmpty();
        results=dogido.fabric.screenshot.BodyViewProtocol.resultBatch(results,bodyViews);
        outbound.add("command_results", results);
        String encoded=dogido.fabric.screenshot.BodyViewProtocol.encodeEvent(outbound,this.gson,
            result->rememberBodyViewResult(ref,result));
        HttpRequest.Builder requestBuilder = HttpRequest.newBuilder(URI.create(this.config.eventEndpoint()))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(encoded));

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
                        bodyViewDelivery ? "body_view_response_omitted" : response.body()
                    );
                    return;
                }
                this.handleAcceptedResponse(response.body(), ref);
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
            this.screenshotState.clearResults();
            invalidateDisplayRequests();
            this.nextRegistrationNanos = 0;
            playerName = this.lastPlayerName;
        }
        this.ensureSession(playerName);
    }

    public void rememberCommandResult(JsonObject result) {
        this.commandState.rememberResult(result);
    }

    private void handleAcceptedResponse(String body, SessionRef ref) {
        observeBodyRequestGeneration(body,ref);
        if(this.bodyViewHandler!=null) {
            java.util.List<String> used;
            synchronized(this) {
                if(!isCurrent(ref))return;
                used=this.bodyViewState.acknowledge(DogidoCommandProtocol.parseAcknowledgedCommandIds(body));
            }
            if(this.bodyViewAcknowledged!=null)used.forEach(this.bodyViewAcknowledged);
            for(var command:dogido.fabric.screenshot.BodyViewProtocol.parseCommands(body)) {
                synchronized(this) {
                    if(!isCurrent(ref))return;
                    if(!command.sessionId().equals(ref.sessionId()) || !this.bodyViewState.claim(command.id()))continue;
                }
                try {this.bodyViewHandler.accept(command,ref);}
                catch(RuntimeException error) {
                    rememberBodyViewResult(ref,dogido.fabric.screenshot.BodyViewProtocol.result(command,"failed","dispatch_failed",java.time.Instant.now()));
                }
            }
        }
        if (this.screenshotHandler != null) {
            synchronized (this) {
                if (!isCurrent(ref)) return;
                this.screenshotState.acknowledge(DogidoCommandProtocol.parseAcknowledgedCommandIds(body));
            }
            for (var command : dogido.fabric.screenshot.ScreenshotProtocol.parseCommands(body)) {
                synchronized (this) {
                    if (!isCurrent(ref)) return;
                    if (!command.sessionId().equals(ref.sessionId()) || !this.screenshotState.claim(command.id())) continue;
                }
                try { this.screenshotHandler.accept(command,ref); }
                catch (RuntimeException error) {
                    rememberScreenshotResult(ref,dogido.fabric.screenshot.ScreenshotProtocol.result(command,
                        "failed","dispatch_failed",java.time.Instant.now()));
                }
            }
        }
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
        if (this.bodyRequestHandler != null) for (var command : dogido.fabric.body.BodyRequestProtocol.parseCommands(body)) {
            synchronized (this) {
                if (!isCurrentBodyRequest(ref,command.inputGeneration())) continue;
                if (!this.commandState.markCommandSeen(command.commandId().toString())) continue;
            }
            try { this.bodyRequestHandler.accept(command,ref); }
            catch (RuntimeException error) {
                rememberBodyRequestResult(ref,dogido.fabric.body.BodyRequestProtocol.result(command,"failed","dispatch_failed",java.time.Instant.now(),command.context()));
            }
        }
        if (this.bodyCommandHandler != null) for (var command : dogido.fabric.body.BodyInspectProtocol.parseCommands(body)) {
            if (!this.commandState.markCommandSeen(command.commandId().toString())) continue;
            try { this.bodyCommandHandler.accept(command,ref); }
            catch (RuntimeException error) {
                this.logger.warn("Dogido body command dispatch failed: command_id={} detail={}",command.commandId(),error.getMessage());
                this.commandState.rememberResult(dogido.fabric.body.BodyInspectProtocol.result(command,"failed","dispatch_failed",java.time.Instant.now()));
            }
        }
        for (HorseRecallProtocol.Command command : HorseRecallProtocol.parseCommands(body)) {
            if (!this.commandState.markCommandSeen(command.commandId())) continue;
            try {
                this.horseCommandHandler.accept(command, ref);
            } catch (RuntimeException error) {
                this.logger.warn("Dogido horse command dispatch failed: command_id={} detail={}",
                    command.commandId(), error.getMessage());
                this.commandState.rememberResult(HorseRecallProtocol.result(command, "failed", "dispatch_failed", java.time.Instant.now()));
            }
        }
    }
}
