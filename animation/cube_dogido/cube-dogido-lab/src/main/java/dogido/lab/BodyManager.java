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
    static Snapshot snapshot(ServerPlayerEntity player) {
        MinecraftServer server = player.getEntityWorld().getServer();
        DogidoEntity body = body(player);
        if (body != null) return body.snapshot();
        UUID id = BodyOwners.get(server).body(player.getUuid());
        return new Snapshot(dogido.lab.api.BodyProtocol.VERSION,id == null ? Presence.NOT_BOUND : Presence.UNLOADED,
            id == null ? null : new Context(player.getUuid(),id,null,null),player.getEntityWorld().getRegistryKey().getValue().toString(),server.getTicks(),0,null,0,
            id == null ? Activity.UNBOUND : Activity.HOLDING,id == null ? "bind_existing_body" : "body_chunk_not_loaded",
            -1,null,null,Sensors.NOT_IMPLEMENTED,null,
            new FoodObservation(ObservationStatus.UNAVAILABLE,server.getTicks(),java.util.List.of(),false));
    }
}
