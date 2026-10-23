package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.WeakHashMap;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.entity.Entity;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import dogido.lab.control.BodyController;
import dogido.lab.control.BodyRecallPolicy;
import dogido.lab.control.CallPlacement;
import dogido.lab.control.BodyTuning;
import net.minecraft.particle.ParticleTypes;
import net.minecraft.util.math.Vec3d;

/** 読み込み済み身体をserver単位で登録し、毎tickの生命処理と不変snapshot取得を仲介する。所有台帳にある未ロード個体は欠損として返し、別個体を自動生成しない。 */
final class BodyManager {
    private static final Map<MinecraftServer,Map<UUID,DogidoEntity>> LOADED = new WeakHashMap<>();
    private BodyManager() {}
    static void initialize() {
        ServerEntityEvents.ENTITY_LOAD.register((entity,world) -> {
            if (entity instanceof DogidoEntity body) {
                LOADED.computeIfAbsent(world.getServer(),s -> new HashMap<>()).put(body.getUuid(),body);
                if (body.ownerId() != null) BodyOwners.get(world.getServer()).claim(body.ownerId(),body.getUuid());
            }
        });
        ServerEntityEvents.ENTITY_UNLOAD.register((entity,world) -> {
            if (entity instanceof DogidoEntity body) {
                body.clearHearing();
                Map<UUID,DogidoEntity> bodies = LOADED.get(world.getServer());
                if (bodies != null) bodies.remove(body.getUuid(),body);
                Entity.RemovalReason reason = body.getRemovalReason();
                if (body.ownerId() != null && (reason == Entity.RemovalReason.KILLED || reason == Entity.RemovalReason.DISCARDED))
                    BodyOwners.get(world.getServer()).release(body.ownerId(),body.getUuid());
            }
        });
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            Map<UUID,DogidoEntity> bodies = LOADED.get(server);
            if (bodies != null) for (DogidoEntity body : java.util.List.copyOf(bodies.values()))
                if (!body.isRemoved()) body.serverStep();
        });
        ServerLifecycleEvents.SERVER_STOPPED.register(LOADED::remove);
    }
    static DogidoEntity body(ServerPlayerEntity player) {
        UUID id = BodyOwners.get(player.getEntityWorld().getServer()).body(player.getUuid());
        return id == null ? null : LOADED.getOrDefault(player.getEntityWorld().getServer(),Map.of()).get(id);
    }
    /** 明示された呼び寄せだけから呼ぶ。既存の所有個体を優先し、身体がない場合に安全な空間で一体を生成・登録する。 */
    static ActionResult recall(ServerPlayerEntity player) { return recall(player,UUID.randomUUID()); }
    static ActionResult recall(ServerPlayerEntity player, UUID actionId) {
        MinecraftServer server = player.getEntityWorld().getServer();
        if (!server.isOnThread()) throw new IllegalStateException("logical_server_thread_required");
        if (server.getPlayerManager().getPlayer(player.getUuid()) != player || !player.isAlive() || player.isSpectator())
            return rejected("owner_unavailable");
        BodyOwners owners = BodyOwners.get(server);
        DogidoEntity existing = body(player);
        var nearby = LOADED.getOrDefault(server,Map.of()).values().stream()
            .filter(b -> !b.isRemoved() && b.isAlive() && b.ownerId() == null
                && b.getEntityWorld() == player.getEntityWorld() && b.squaredDistanceTo(player) <= 64).toList();
        switch (BodyRecallPolicy.resolve(owners.body(player.getUuid()) != null,existing != null,nearby.size())) {
            case EXISTING -> { return existing.localCall(player,actionId); }
            case UNLOADED -> { return rejected("body_chunk_not_loaded"); }
            case AMBIGUOUS -> { return rejected("multiple_unbound_bodies"); }
            case BIND_NEARBY -> {
                DogidoEntity found = nearby.getFirst();
                String result = found.bind(player);
                return result.equals("bound") ? found.localCall(player,actionId) : rejected(result);
            }
            case SPAWN -> { }
        }
        DogidoEntity created = new DogidoEntity(DogidoLab.DOGIDO,player.getEntityWorld());
        BodySpace space = new BodySpace(player.getEntityWorld(),created);
        var pose = new BodyController.Owner(player.getEntityPos(),player.getYaw(),
            Math.max(.1,player.getEyeY()-player.getY()-.2-BodyTuning.BODY_SIZE/2),true);
        var safety = space.safety(player,player.getEntityPos());
        Vec3d destination = CallPlacement.candidates(pose,2).stream()
            .filter(p -> space.target(p,false,player,false) && safety.safeTarget(p)).findFirst().orElse(null);
        if (destination == null) return rejected("no_safe_spawn_destination");
        created.setPosition(destination); created.setYaw(player.getYaw()+180);
        String binding = created.bind(player);
        if (!binding.equals("bound")) return rejected(binding);
        boolean spawned = false;
        try {
            spawned = player.getEntityWorld().spawnEntity(created);
            if (!spawned) return rejected("body_spawn_failed");
        } finally {
            // falseだけでなく生成途中の例外でも、今回の個体と所有登録を残さない。
            if (!spawned) {
                created.discard();
                owners.release(player.getUuid(),created.getUuid());
            }
        }
        // 実生成と所有登録に成功した個体だけ演出する。パーティクルを接近の到着結果として扱わない。
        player.getEntityWorld().spawnParticles(ParticleTypes.END_ROD,destination.x,destination.y+.3,destination.z,
            36,.45,.45,.45,.035);
        return created.localCall(player,actionId);
    }
    private static ActionResult rejected(String reason) { return new ActionResult(null,-1,Outcome.REJECTED,reason); }
    /** server発音の実座標を同じ世界で読込み済みの身体へ渡す。各身体が所有関係・到達距離を検証する。 */
    static void recordSound(ServerWorld world, Position source, String soundId, String category,
            float volume, Float pitch, double range, UUID sourceEntityId) {
        Map<UUID,DogidoEntity> bodies = LOADED.get(world.getServer());
        if (bodies == null) return;
        long tick = world.getServer().getTicks();
        for (DogidoEntity body : bodies.values())
            if (!body.isRemoved() && body.getEntityWorld() == world)
                body.hearSound(tick,source,soundId,category,volume,pitch,range,sourceEntityId);
    }
    static Snapshot snapshot(ServerPlayerEntity player) {
        MinecraftServer server = player.getEntityWorld().getServer();
        DogidoEntity body = body(player);
        if (body != null) return body.snapshot();
        UUID id = BodyOwners.get(server).body(player.getUuid());
        return new Snapshot(dogido.lab.api.BodyProtocol.VERSION,id == null ? Presence.NOT_BOUND : Presence.UNLOADED,
            id == null ? null : new Context(player.getUuid(),id,null,null),player.getEntityWorld().getRegistryKey().getValue().toString(),server.getTicks(),0,null,0,
            id == null ? Activity.UNBOUND : Activity.HOLDING,id == null ? "bind_existing_body" : "body_chunk_not_loaded",
            -1,null,null,Sensors.BODY_VISION_AND_HEARING,null,
            new FoodObservation(ObservationStatus.UNAVAILABLE,server.getTicks(),java.util.List.of(),false),null,
            BodyVision.unavailable(server.getTicks(),id == null ? "invalid_pose" : "area_unloaded"),
            new HearingObservation(ObservationStatus.UNAVAILABLE,server.getTicks(),java.util.List.of(),false));
    }
}
