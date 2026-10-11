package dogido.fabric.protocol;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;
import java.util.function.Supplier;
import net.minecraft.client.MinecraftClient;

/** One snapshot poll, with an ordered ACK/input/voice lane independent of the workshop HUD. */
public final class DogidoDialogueClient {
    public record Operation(DialoguePayload.Acknowledgement acknowledgement, DialoguePayload.Reply reply) {}
    private final DogidoEventClient events;
    private final AtomicBoolean pollInFlight = new AtomicBoolean();
    private final AtomicLong pollGeneration = new AtomicLong();
    private final AtomicInteger mutations = new AtomicInteger();
    private CompletableFuture<Void> operationTail = CompletableFuture.completedFuture(null);
    private DogidoEventClient.SessionRef operationSession;

    public DogidoDialogueClient(DogidoEventClient events) { this.events = events; }

    public void invalidatePoll() { this.pollGeneration.incrementAndGet(); }

    public void poll(DogidoEventClient.SessionRef ref, Consumer<DialoguePayload.Snapshot> handler) {
        long generation = this.pollGeneration.get();
        if (ref == null || this.mutations.get() > 0 || !this.pollInFlight.compareAndSet(false, true)) return;
        this.events.dialogueRequest(ref, "/api/v1/rust-dialogue/snapshot?session_id="
                + URLEncoder.encode(ref.sessionId(), StandardCharsets.UTF_8), null)
            .whenComplete((response, error) -> {
                DialoguePayload.Snapshot snapshot = error == null && response.statusCode() == 200
                    ? DialoguePayload.snapshot(response.body(), ref.sessionId()) : null;
                MinecraftClient.getInstance().execute(() -> {
                    try {
                        if (snapshot != null && this.events.isCurrent(ref) && generation == this.pollGeneration.get()) {
                            handler.accept(snapshot);
                        }
                    } finally {
                        this.pollInFlight.set(false);
                    }
                });
            });
    }

    public CompletableFuture<DialoguePayload.Acknowledgement> acknowledge(DogidoEventClient.SessionRef ref, List<String> ids) {
        return enqueue(ref, () -> sendAcknowledgement(ref, ids));
    }

    public CompletableFuture<Operation> text(DogidoEventClient.SessionRef ref, List<String> displayed, String text) {
        JsonObject payload = sessionPayload(ref);
        payload.addProperty("text", text);
        payload.addProperty("source", "text");
        return mutate(ref, displayed, "/api/v1/player-input", payload);
    }

    public CompletableFuture<Operation> voice(DogidoEventClient.SessionRef ref, List<String> displayed, boolean enabled) {
        JsonObject payload = sessionPayload(ref);
        payload.addProperty("enabled", enabled);
        return mutate(ref, displayed, "/api/v1/rust-dialogue/voice", payload);
    }

    private CompletableFuture<Operation> mutate(DogidoEventClient.SessionRef ref, List<String> displayed,
                                                String path, JsonObject payload) {
        this.mutations.incrementAndGet();
        invalidatePoll();
        return enqueue(ref, () -> sendAcknowledgement(ref, displayed).thenCompose(ack -> {
            if (!ack.covers(displayed)) return CompletableFuture.completedFuture(
                new Operation(ack, DialoguePayload.Reply.failure("display_ack_failed")));
            return this.events.dialogueRequest(ref, path, payload)
                .handle((response, error) -> new Operation(ack, error != null
                    ? DialoguePayload.Reply.failure("connection_failed")
                    : response.statusCode() / 100 != 2
                        ? rejectedHttp(response.statusCode(), response.body())
                        : DialoguePayload.reply(response.body())));
        })).whenComplete((result, error) -> {
            invalidatePoll();
            this.mutations.decrementAndGet();
        });
    }

    private CompletableFuture<DialoguePayload.Acknowledgement> sendAcknowledgement(DogidoEventClient.SessionRef ref,
                                                                                  List<String> ids) {
        if (ids.isEmpty()) return CompletableFuture.completedFuture(
            new DialoguePayload.Acknowledgement(true, List.of(), List.of()));
        JsonObject payload = sessionPayload(ref);
        JsonArray array = new JsonArray();
        ids.forEach(array::add);
        payload.add("display_ids", array);
        return this.events.dialogueRequest(ref, "/api/v1/rust-dialogue/displayed", payload)
            .handle((response, error) -> error == null && response.statusCode() / 100 == 2
                ? DialoguePayload.acknowledgement(response.body()) : DialoguePayload.Acknowledgement.failed());
    }

    private synchronized <T> CompletableFuture<T> enqueue(DogidoEventClient.SessionRef ref, Supplier<CompletableFuture<T>> task) {
        if (!ref.equals(this.operationSession)) {
            this.operationSession = ref;
            this.operationTail = CompletableFuture.completedFuture(null);
        }
        CompletableFuture<T> result = this.operationTail.handle((ignored, error) -> null).thenCompose(ignored -> {
            if (!this.events.isCurrent(ref)) return CompletableFuture.failedFuture(new IllegalStateException("stale_session"));
            return task.get();
        });
        this.operationTail = result.handle((ignored, error) -> null);
        return result;
    }

    private static JsonObject sessionPayload(DogidoEventClient.SessionRef ref) {
        JsonObject payload = new JsonObject();
        payload.addProperty("session_id", ref.sessionId());
        return payload;
    }

    private static DialoguePayload.Reply rejectedHttp(int status, String body) {
        DialoguePayload.Reply reply = DialoguePayload.reply(body);
        return DialoguePayload.Reply.failure(!reply.reason().isBlank() && !"invalid_response".equals(reply.reason())
            ? reply.reason() : "http_" + status);
    }
}
