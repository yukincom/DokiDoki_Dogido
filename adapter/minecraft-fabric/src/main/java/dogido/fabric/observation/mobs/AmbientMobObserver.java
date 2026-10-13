package dogido.fabric.observation.mobs;

import com.google.gson.JsonObject;
import dogido.fabric.observation.MobEnvironmentObservation;
import dogido.fabric.observation.NearbyComposition;
import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.mobs.MobClassifier.MobDisposition;
import dogido.fabric.observation.mobs.MobClassifier;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.passive.VillagerEntity;
import net.minecraft.registry.Registries;
import net.minecraft.registry.entry.RegistryEntry;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.Vec3d;
import net.minecraft.village.VillagerProfession;
import net.minecraft.village.VillagerType;
import org.slf4j.Logger;

import static dogido.fabric.observation.MobIdentityObservation.mobIdentity;
import static dogido.fabric.observation.ObservationGeometry.classifyHorizontal;
import static dogido.fabric.observation.ObservationGeometry.classifyVertical;
import static dogido.fabric.observation.ObservationVisibility.hasLineOfSight;

public final class AmbientMobObserver {
    private static final double AMBIENT_MOB_DISTANCE = 16.0;
    private final NearbyComposition.RangeTracker nearbyMobKinds =
        new NearbyComposition.RangeTracker(NearbyComposition.MOB_ENTER, NearbyComposition.MOB_EXIT);
    private JsonObject nearbyMobComposition = NearbyComposition.mobs(List.of());

    private final MobClassifier mobClassifier;
    private final Logger logger;

    public AmbientMobObserver(MobClassifier mobClassifier, Logger logger) {
        this.mobClassifier = mobClassifier;
        this.logger = logger;
    }

    public void resetRangeTracking() { this.nearbyMobKinds.reset(); this.nearbyMobComposition = NearbyComposition.mobs(List.of()); }
    public JsonObject copyNearbyComposition() { return this.nearbyMobComposition.deepCopy(); }

    public List<AmbientMobObservation> scanAmbientMobs(ClientPlayerEntity player, ClientWorld world, boolean captureNearbyKinds) {
        return scanAmbientMobs(ObservationOrigin.player(player),world,captureNearbyKinds);
    }
    /** 受け手の範囲・視線から実視認だけを集める。種類保持はこのcollectorの所有者ごとに独立する。 */
    public List<AmbientMobObservation> scanAmbientMobs(ObservationOrigin origin, ClientWorld world, boolean captureNearbyKinds) {
        List<Entity> entities = world.getOtherEntities(
            origin.player(),
            origin.bounds().expand(captureNearbyKinds ? NearbyComposition.MOB_EXIT : AMBIENT_MOB_DISTANCE),
            MobClassifier::isAmbientMobCandidate
        );

        List<AmbientMobObservation> mobs = new ArrayList<>();
        for (Entity entity : entities) {
            if (origin.isSelf(entity) || !(entity instanceof LivingEntity living)) {
                continue;
            }
            MobDisposition disposition = this.mobClassifier.classifyMobDisposition(origin.threatTargetId(), living);
            if (!disposition.ambientEligible()) {
                continue;
            }
            double distance = origin.distanceTo(entity);
            if (distance > (captureNearbyKinds ? NearbyComposition.MOB_EXIT : AMBIENT_MOB_DISTANCE)) {
                continue;
            }
            // 外周の新規種類は今回の表に入らないので、追加の視線検査をしない。
            if (distance > AMBIENT_MOB_DISTANCE && !this.nearbyMobKinds.allows(disposition.type(), distance)) continue;
            if (!origin.visible(world,entity,captureNearbyKinds ? NearbyComposition.MOB_EXIT : AMBIENT_MOB_DISTANCE)) {
                continue;
            }

            Vec3d entityPosition = new Vec3d(entity.getX(), entity.getY(), entity.getZ());
            Boolean isBaby = null;
            String profession = null;
            String villagerType = null;
            if (living instanceof VillagerEntity villager) {
                isBaby = villager.isBaby();
                profession = villagerProfessionId(villager);
                villagerType = villagerTypeId(villager);
            }
            mobs.add(
                new AmbientMobObservation(
                    entity.getUuid(),
                    disposition.type(),
                    distance,
                    classifyHorizontal(origin, entityPosition),
                    classifyVertical(origin, entityPosition),
                    disposition.temperament(),
                    disposition.cautionReason(),
                    isBaby,
                    profession,
                    villagerType,
                    mobIdentity(entity),
                    MobEnvironmentObservation.capture(entity)
                )
            );
        }

        // Routine encounter priority stays unchanged. Extra companions are retained only as conversation candidates.
        mobs.sort(Comparator.comparing((AmbientMobObservation mob) -> mob.identity().tamed())
            .thenComparingDouble(AmbientMobObservation::distance));
        if (captureNearbyKinds) {
            var visible = mobs.stream()
                .filter(mob -> this.nearbyMobKinds.allows(mob.type(), mob.distance())).toList();
            this.nearbyMobKinds.finish(visible.stream().map(AmbientMobObservation::type).toList());
            this.nearbyMobComposition = NearbyComposition.mobs(visible.stream()
                .map(mob -> new NearbyComposition.Mob(mob.type(), Boolean.TRUE.equals(mob.isBaby()), mob.profession()))
                .toList());
        }
        // 会話の種類表だけ外周まで保つ。個体一覧・村人数・従来の材料の範囲は変えない。
        return mobs.stream().filter(mob -> mob.distance() <= AMBIENT_MOB_DISTANCE).toList();
    }

    /**
     * 村人 profession の短名（farmer / none / nitwit 等）。
     *
     * RegistryEntry はクライアント同期だと getKey() が empty のことがある。
     * 1) matchesKey(VillagerProfession.*)  2) getKey  3) Registries.getId(value)
     * の順で取る。
     */
    private String villagerProfessionId(VillagerEntity villager) {
        RegistryEntry<VillagerProfession> entry = villager.getVillagerData().profession();
        if (entry.matchesKey(VillagerProfession.NONE)) {
            return "none";
        }
        if (entry.matchesKey(VillagerProfession.NITWIT)) {
            return "nitwit";
        }
        if (entry.matchesKey(VillagerProfession.ARMORER)) {
            return "armorer";
        }
        if (entry.matchesKey(VillagerProfession.BUTCHER)) {
            return "butcher";
        }
        if (entry.matchesKey(VillagerProfession.CARTOGRAPHER)) {
            return "cartographer";
        }
        if (entry.matchesKey(VillagerProfession.CLERIC)) {
            return "cleric";
        }
        if (entry.matchesKey(VillagerProfession.FARMER)) {
            return "farmer";
        }
        if (entry.matchesKey(VillagerProfession.FISHERMAN)) {
            return "fisherman";
        }
        if (entry.matchesKey(VillagerProfession.FLETCHER)) {
            return "fletcher";
        }
        if (entry.matchesKey(VillagerProfession.LEATHERWORKER)) {
            return "leatherworker";
        }
        if (entry.matchesKey(VillagerProfession.LIBRARIAN)) {
            return "librarian";
        }
        if (entry.matchesKey(VillagerProfession.MASON)) {
            return "mason";
        }
        if (entry.matchesKey(VillagerProfession.SHEPHERD)) {
            return "shepherd";
        }
        if (entry.matchesKey(VillagerProfession.TOOLSMITH)) {
            return "toolsmith";
        }
        if (entry.matchesKey(VillagerProfession.WEAPONSMITH)) {
            return "weaponsmith";
        }
        Optional<String> fromKey = entry.getKey().map(key -> key.getValue().getPath());
        if (fromKey.isPresent() && !fromKey.get().isBlank()) {
            return fromKey.get();
        }
        Identifier id = Registries.VILLAGER_PROFESSION.getId(entry.value());
        if (id != null && id.getPath() != null && !id.getPath().isBlank()) {
            return id.getPath();
        }
        // 取れないときは null（サーバは「村人」汎用に落とす。誤って求職者にしない）
        this.logger.debug("villager profession unresolved idAsString={}", entry.getIdAsString());
        return null;
    }

    private String villagerTypeId(VillagerEntity villager) {
        RegistryEntry<VillagerType> entry = villager.getVillagerData().type();
        Optional<String> fromKey = entry.getKey().map(key -> key.getValue().getPath());
        if (fromKey.isPresent() && !fromKey.get().isBlank()) {
            return fromKey.get();
        }
        Identifier id = Registries.VILLAGER_TYPE.getId(entry.value());
        if (id != null && id.getPath() != null && !id.getPath().isBlank()) {
            return id.getPath();
        }
        return null;
    }
}
