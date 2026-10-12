package dogido.fabric.horse_recall;

import com.google.gson.JsonObject;
import dogido.fabric.protocol.DogidoEventClient;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.function.Predicate;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.entity.passive.AbstractHorseEntity;
import net.minecraft.entity.passive.CamelEntity;
import net.minecraft.entity.EntityPose;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.Vec3d;
import org.slf4j.Logger;

/**
 * 統合サーバーの乗れる動物の観測と一度限りの呼戻しを、既存sessionとcommand結果へ接続する。
 * clientは接続世代と演出、serverは所有照合・足場・移動を所有し、相互には不変値を渡す。
 */
public final class HorseRecallRuntime {
    private final Logger logger;
    private final Consumer<JsonObject> resultSink;
    private final Predicate<DogidoEventClient.SessionRef> currentSession;
    private final AtomicReference<Operation> active = new AtomicReference<>();
    private volatile Scope scope;
    private volatile HorseRecallProtocol.Snapshot snapshot;
    private HorseRecallEffect effect;
    private long clientTicks;

    private static final class Scope {
        final UUID worldId = UUID.randomUUID();
        final UUID playerId;
        final String dimension;
        final ClientWorld clientWorld;
        final Object connection;
        final MinecraftServer server;
        final AtomicLong revision = new AtomicLong();
        final AtomicBoolean observing = new AtomicBoolean();
        UUID recentMount; // server threadのみが更新する。
        long nextObservation; // client threadのみが更新する。

        Scope(MinecraftClient client) {
            playerId = client.player.getUuid();
            dimension = client.world.getRegistryKey().getValue().toString();
            clientWorld = client.world;
            connection = client.getNetworkHandler();
            server = client.getServer();
        }

        boolean matches(MinecraftClient client) {
            return client.world == clientWorld && client.getNetworkHandler() == connection
                && client.getServer() == server && client.player != null && client.player.getUuid().equals(playerId);
        }
    }

    private static final class Operation {
        final HorseRecallProtocol.Command command;
        final DogidoEventClient.SessionRef session;
        final Scope scope;
        final HorseRecallLifecycle lifecycle = new HorseRecallLifecycle();
        HorseRecallLanding.Point landing; // serverが確定した後だけclientへ渡す。

        Operation(HorseRecallProtocol.Command command, DogidoEventClient.SessionRef session, Scope scope) {
            this.command = command;
            this.session = session;
            this.scope = scope;
        }
    }

    public HorseRecallRuntime(Logger logger, Consumer<JsonObject> resultSink,
            Predicate<DogidoEventClient.SessionRef> currentSession) {
        this.logger = logger;
        this.resultSink = resultSink;
        this.currentSession = currentSession;
        ServerTickEvents.END_SERVER_TICK.register(this::serverTick);
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> clear());
    }

    public boolean available() { return scope != null && scope.server != null; }

    /** status_snapshotだけが取得する。古いserver callbackは新scopeへ公開しない。 */
    public JsonObject snapshotJson() {
        HorseRecallProtocol.Snapshot current = snapshot;
        Scope currentScope = scope;
        return current == null || currentScope == null || !current.worldId().equals(currentScope.worldId)
            ? null : current.toJson();
    }

    /** END_CLIENT_TICKで呼ぶ。world/接続が変われば先に旧操作を取消し、正本観測を非同期要求する。 */
    public void tick(MinecraftClient client) {
        if (client.player == null || client.world == null || client.getNetworkHandler() == null) {
            clear();
            return;
        }
        if (scope == null || !scope.matches(client)) {
            clear();
            Scope next = new Scope(client);
            scope = next;
            snapshot = unavailable(next);
        }
        clientTicks++;
        Scope current = scope;
        if (clientTicks >= current.nextObservation && current.observing.compareAndSet(false, true)) {
            current.nextObservation = clientTicks + 20;
            if (current.server == null) {
                snapshot = unavailable(current);
                current.observing.set(false);
            } else {
                current.server.execute(() -> observe(current));
            }
        }
        Operation operation = active.get();
        if (operation != null && !validScope(operation)) reject(operation, "rejected", "stale_world");
        if (operation != null && (operation.lifecycle.stage() == HorseRecallLifecycle.Stage.ACCEPTED
                || operation.lifecycle.stage() == HorseRecallLifecycle.Stage.PREVIEW)) {
            String timeError = operation.command.timeError(Instant.now());
            if (timeError != null) reject(operation, status(timeError), timeError);
        }
        if (effect != null && effect.tick(client)) {
            Operation completed = active.get();
            if (completed != null && effect.lifecycle == completed.lifecycle) active.compareAndSet(completed, null);
            effect = null;
        }
    }

    /** 接続変更・無効化で呼ぶ。移動後は既存の成功結果を保持し、残りの演出だけを止める。 */
    public void clear() {
        scope = null;
        snapshot = null;
        Operation operation = active.getAndSet(null);
        if (operation != null) reject(operation, "rejected", "stale_world");
        if (effect != null) {
            effect.cancel();
            effect = null;
        }
    }

    /** HTTP callbackから受け、client世代を再照合してからserver threadへ渡す。 */
    public void handle(HorseRecallProtocol.Command command, DogidoEventClient.SessionRef session) {
        MinecraftClient client = MinecraftClient.getInstance();
        client.execute(() -> {
            Scope current = scope;
            Operation operation = new Operation(command, session, current);
            if (current == null || current.server == null || !current.matches(client)) {
                reject(operation, "rejected", "integrated_server_unavailable");
                return;
            }
            if (!validScope(operation)) {
                reject(operation, "rejected", "stale_world");
                return;
            }
            if (!active.compareAndSet(null, operation)) {
                reject(operation, "rejected", "recall_in_progress");
                return;
            }
            current.server.execute(() -> prepare(operation));
        });
    }

    private HorseRecallProtocol.Snapshot unavailable(Scope current) {
        return new HorseRecallProtocol.Snapshot(false, current.worldId, current.revision.incrementAndGet(), Instant.now(),
            current.playerId, current.dimension, null, List.of());
    }

    private void observe(Scope current) {
        try {
            if (scope != current) return;
            ServerPlayerEntity player = current.server.getPlayerManager().getPlayer(current.playerId);
            if (player == null || !player.getEntityWorld().getRegistryKey().getValue().toString().equals(current.dimension)) {
                if (scope == current) snapshot = unavailable(current);
                return;
            }
            if (player.getVehicle() instanceof MobEntity horse && HorseRecallWorld.authorized(horse, current.playerId))
                current.recentMount = horse.getUuid();
            List<HorseRecallProtocol.Candidate> candidates = HorseRecallWorld.observe(player.getEntityWorld(), current.playerId);
            var captured = new HorseRecallProtocol.Snapshot(true, current.worldId, current.revision.incrementAndGet(),
                Instant.now(), current.playerId, current.dimension, current.recentMount, candidates);
            if (scope == current) snapshot = captured;
        } catch (RuntimeException error) {
            if (scope == current) snapshot = unavailable(current);
            logger.warn("Dogido horse observation failed: {}", error.getMessage());
        } finally {
            current.observing.set(false);
        }
    }

    private boolean validScope(Operation operation) {
        Scope current = operation.scope;
        return current != null && scope == current && current.server != null
            && currentSession.test(operation.session) && current.worldId.equals(operation.command.worldId())
            && current.playerId.equals(operation.command.playerUuid())
            && current.dimension.equals(operation.command.dimension());
    }

    private String validate(Operation operation) {
        if (!validScope(operation)) return "stale_world";
        String timeError = operation.command.timeError(Instant.now());
        if (timeError != null) return timeError;
        ServerPlayerEntity player = operation.scope.server.getPlayerManager().getPlayer(operation.scope.playerId);
        String playerError = HorseRecallWorld.playerError(player, operation.command.dimension(),
            player == null ? null : player.getEntityWorld().getEntity(operation.command.entityId()));
        if (playerError != null) return playerError;
        return HorseRecallWorld.horseError(player.getEntityWorld().getEntity(operation.command.entityId()), player);
    }

    private void prepare(Operation operation) {
        try {
            String error = validate(operation);
            if (error != null) { reject(operation, status(error), error); return; }
            ServerPlayerEntity player = operation.scope.server.getPlayerManager().getPlayer(operation.scope.playerId);
            ServerWorld world = player.getEntityWorld();
            MobEntity horse = (MobEntity)world.getEntity(operation.command.entityId());
            operation.landing = HorseRecallLanding.find(player.getX(), player.getY(), player.getZ(),
                point -> HorseRecallWorld.safe(world, horse, point));
            if (operation.landing == null) {
                reject(operation, "rejected", HorseRecallMounts.kind(horse).aquatic() ? "no_water_landing" : "no_safe_landing");
                return;
            }
            // server個体をclient threadから読まず、演出寸法の不変値だけを渡す。
            var dimensions = horse.getDimensions(EntityPose.STANDING);
            MinecraftClient.getInstance().execute(() -> {
                if (!validScope(operation) || active.get() != operation) { reject(operation, "rejected", "stale_world"); return; }
                if (operation.lifecycle.startPreview()) {
                    // 前操作の取消と次操作の準備が同じclient tickに届いても、残った粒子を引き継がない。
                    if (effect != null) effect.cancel();
                    effect = new HorseRecallEffect(operation.command, operation.landing, operation.lifecycle, logger,
                        dimensions.width(), dimensions.height());
                    logger.info("Dogido horse presentation: command_id={} stage=preview_started", operation.command.commandId());
                }
            });
        } catch (RuntimeException error) {
            logger.warn("Dogido horse prepare failed: {}", error.getMessage());
            reject(operation, "failed", "prepare_failed");
        }
    }

    private void serverTick(MinecraftServer server) {
        Operation operation = active.get();
        if (operation == null || operation.scope.server != server) return;
        try {
            synchronized (operation.lifecycle) {
                if (operation.lifecycle.stage() != HorseRecallLifecycle.Stage.PREVIEW) return;
                String error = validate(operation);
                if (error != null) { reject(operation, status(error), error); return; }
                ServerPlayerEntity player = server.getPlayerManager().getPlayer(operation.scope.playerId);
                ServerWorld world = player.getEntityWorld();
                MobEntity horse = (MobEntity)world.getEntity(operation.command.entityId());
                var point = operation.landing;
                if (!point.near(player.getX(), player.getY(), player.getZ()) || !HorseRecallWorld.safe(world, horse, point)) {
                    reject(operation, "rejected", "landing_changed");
                    return;
                }
                if (!operation.lifecycle.previewTick()) return;
                if (!validScope(operation)) { reject(operation, "rejected", "stale_world"); return; }
                // 同じServerWorldの同じUUIDだけを移動する。再生成や別次元teleportには進まない。
                if (!horse.teleport(world, point.x(), point.y(), point.z(), Set.of(), horse.getYaw(), horse.getPitch(), false)
                        || world.getEntity(horse.getUuid()) != horse
                        || horse.squaredDistanceTo(point.x(), point.y(), point.z()) > 0.01) {
                    reject(operation, "failed", "teleport_not_applied");
                    return;
                }
                operation.lifecycle.moved();
                result(operation, "succeeded", "recalled");
                // ここから先は到着後の整形と演出。失敗しても実移動の成功を上書きしない。
                horse.getNavigation().stop();
                horse.setVelocity(Vec3d.ZERO);
                horse.onLanding();
                var kind = HorseRecallMounts.kind(horse);
                horse.setOnGround(!kind.aquatic() && !kind.flying());
                if (kind.rears() && horse instanceof AbstractHorseEntity equine) equine.setAngry(20);
                if (horse instanceof CamelEntity camel && camel.isSitting()) camel.setStanding();
            }
        } catch (RuntimeException error) {
            logger.warn("Dogido horse recall failed: {}", error.getMessage());
            reject(operation, "failed", "execution_failed");
        }
    }

    private static String status(String error) { return "expired".equals(error) ? "expired" : "rejected"; }

    private void reject(Operation operation, String status, String detail) {
        operation.lifecycle.cancel();
        result(operation, status, detail);
        active.compareAndSet(operation, null);
        logger.info("Dogido horse presentation: command_id={} stage=cancelled detail={}", operation.command.commandId(), detail);
    }

    private void result(Operation operation, String status, String detail) {
        if (!operation.lifecycle.recordResult()) return;
        resultSink.accept(HorseRecallProtocol.result(operation.command, status, detail, Instant.now()));
        logger.info("Dogido command result: command_id={} status={} detail={}", operation.command.commandId(), status, detail);
    }
}
