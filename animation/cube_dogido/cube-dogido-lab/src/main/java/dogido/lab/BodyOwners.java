package dogido.lab;

import com.mojang.serialization.Codec;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import net.minecraft.server.MinecraftServer;
import net.minecraft.util.Uuids;
import net.minecraft.world.PersistentState;
import net.minecraft.world.PersistentStateType;

/** A saved one-owner/one-body index also protects bodies in unloaded chunks from duplication. */
final class BodyOwners extends PersistentState {
    private static final Codec<BodyOwners> CODEC = Codec.unboundedMap(Uuids.STRING_CODEC,Uuids.STRING_CODEC)
        .xmap(BodyOwners::new, state -> state.owners);
    private static final PersistentStateType<BodyOwners> TYPE = new PersistentStateType<>(
        "dogido_body_owners",BodyOwners::new,CODEC,null);
    private final Map<UUID,UUID> owners;
    private BodyOwners() { this(Map.of()); }
    private BodyOwners(Map<UUID,UUID> owners) { this.owners = new HashMap<>(owners); }
    static BodyOwners get(MinecraftServer server) { return server.getOverworld().getPersistentStateManager().getOrCreate(TYPE); }
    UUID body(UUID owner) { return owners.get(owner); }
    boolean claim(UUID owner, UUID body) {
        UUID old = owners.get(owner);
        if (old != null && !old.equals(body)) return false;
        if (old == null) { owners.put(owner,body); markDirty(); }
        return true;
    }
    void release(UUID owner, UUID body) {
        if (owners.remove(owner,body)) markDirty();
    }
}
