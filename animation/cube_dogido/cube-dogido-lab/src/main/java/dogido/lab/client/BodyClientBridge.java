package dogido.lab.client;

import com.google.gson.Gson;
import dogido.lab.BodyPackets;
import dogido.lab.api.BodyBridge;
import dogido.lab.api.BodyProtocol;
import dogido.lab.api.BodyProtocol.*;
import java.util.Objects;
import java.util.Optional;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.client.world.ClientWorld;

/** 任意Java entrypointとして不変snapshotを保持し、client threadで渡された要求をFabric通信へ送る。世代と受信後の鮮度を検査し、HTTP workerからはsnapshotの読み取りだけを許す。 */
public final class BodyClientBridge implements BodyBridge {
    private static final Gson JSON = new Gson();
    private static volatile Snapshot latest;
    private static volatile long receivedAt;
    private static ClientWorld world;
    private static ClientPlayNetworkHandler connection;
    private static int pollTick;

    public static void initialize() {
        ClientPlayNetworking.registerGlobalReceiver(BodyPackets.Response.ID,(payload,context) -> {
            ClientPlayNetworkHandler receivedConnection = context.client().getNetworkHandler();
            ClientWorld receivedWorld = context.client().world;
            context.client().execute(() -> {
                MinecraftClient client = context.client();
                if (client.getNetworkHandler() != receivedConnection || client.world != receivedWorld || client.player == null || client.world == null) return;
                try {
                    Snapshot next = JSON.fromJson(payload.json(),Snapshot.class);
                    if (next == null || next.version() != BodyProtocol.VERSION || next.presence() == null
                            || !client.world.getRegistryKey().getValue().toString().equals(next.ownerDimension())) return;
                    if (next.context() != null && !client.player.getUuid().equals(next.context().ownerId())) return;
                    Snapshot old = latest;
                    if (old != null && Objects.equals(old.context(),next.context()) && next.snapshotRevision() < old.snapshotRevision()) return;
                    latest = next;
                    receivedAt = System.nanoTime();
                } catch (RuntimeException ignored) { }
            });
        });
        ClientPlayConnectionEvents.DISCONNECT.register((handler,client) -> clear());
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            if (client.world != world || client.getNetworkHandler() != connection) {
                clear(); world = client.world; connection = client.getNetworkHandler();
            }
            if (client.player != null && client.world != null && ++pollTick % 10 == 0 && ClientPlayNetworking.canSend(BodyPackets.Request.ID))
                ClientPlayNetworking.send(new BodyPackets.Request(JSON.toJson(new BodyPackets.Message(BodyProtocol.VERSION,"snapshot",null,null))));
        });
    }
    private static void clear() { latest = null; receivedAt = 0; world = null; connection = null; pollTick = 0; }
    @Override public int protocolVersion() { return BodyProtocol.VERSION; }
    @Override public boolean available() {
        MinecraftClient client = MinecraftClient.getInstance();
        return client.player != null && client.world != null && ClientPlayNetworking.canSend(BodyPackets.Request.ID);
    }
    @Override public Optional<Snapshot> snapshot() {
        return System.nanoTime()-receivedAt > 3_000_000_000L ? Optional.empty() : Optional.ofNullable(latest);
    }
    private Submission ready(Context context) {
        if (!MinecraftClient.getInstance().isOnThread()) return Submission.WRONG_THREAD;
        if (!available()) return Submission.UNAVAILABLE;
        Snapshot snapshot = snapshot().orElse(null);
        return snapshot == null || snapshot.presence() != Presence.PRESENT || !Objects.equals(snapshot.context(),context)
            ? Submission.STALE_CONTEXT : Submission.QUEUED;
    }
    @Override public Submission applyState(StateRequest request) {
        Submission result = ready(request == null ? null : request.context());
        if (result == Submission.QUEUED) ClientPlayNetworking.send(new BodyPackets.Request(JSON.toJson(new BodyPackets.Message(BodyProtocol.VERSION,"state",request,null))));
        return result;
    }
    @Override public Submission requestAction(ActionRequest request) {
        Submission result = ready(request == null ? null : request.context());
        if (result == Submission.QUEUED) ClientPlayNetworking.send(new BodyPackets.Request(JSON.toJson(new BodyPackets.Message(BodyProtocol.VERSION,"action",null,request))));
        return result;
    }
}
