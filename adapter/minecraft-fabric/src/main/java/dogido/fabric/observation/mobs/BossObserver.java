package dogido.fabric.observation.mobs;

import com.google.gson.JsonObject;
import dogido.fabric.observation.WorldObservationFrame;
import net.minecraft.util.math.Box;
import dogido.fabric.observation.mobs.BossTracker;
import dogido.fabric.observation.mobs.ThreatObservation;
import java.util.List;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.boss.dragon.EnderDragonEntity;
import net.minecraft.entity.boss.dragon.phase.PhaseType;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.Vec3d;

import static dogido.fabric.observation.ObservationGeometry.classifyHorizontal;
import static dogido.fabric.observation.ObservationGeometry.classifyVertical;
import static dogido.fabric.observation.ObservationNumbers.round;
import static dogido.fabric.observation.ObservationTime.ticksSince;

/** ボスの観測履歴とイベント情報を作る。周辺entityの実取得はtick所有のframeへ集約し、履歴だけをこの観測器が更新する。 */
public final class BossObserver {
    private static final int WARDEN_SPECIAL_LATCH_TICKS = 100;
    // ドラゴン戦: 視覚脅威の30マス制限とは別に、アリーナ全域を追跡する
    private static final double DRAGON_SCAN_RADIUS = 160.0;
    private static final int DRAGON_DEFEAT_WINDOW_TICKS = 400;

    private final BossTracker bossTracker;
    private final java.util.function.LongSupplier tickSource;
    private final java.util.function.LongSupplier lastExplosionTick;

    public BossObserver(BossTracker bossTracker, java.util.function.LongSupplier tickSource,
            java.util.function.LongSupplier lastExplosionTick) {
        this.bossTracker = bossTracker;
        this.tickSource = tickSource;
        this.lastExplosionTick = lastExplosionTick;
    }

    public void observeWardenSpecialSetups(
        ClientPlayerEntity player,
        WorldObservationFrame frame,
        List<ThreatObservation> threats
    ) {
        ThreatObservation warden = nearestThreatOfType(threats, "warden");
        boolean recentWardenContext = warden != null
            || (this.bossTracker.lastWardenSeenTick() >= 0 && this.tickSource.getAsLong() - this.bossTracker.lastWardenSeenTick() <= 200);
        if (!recentWardenContext) {
            return;
        }

        int nearbyEndCrystalCount = countNearbyEntityTypeAroundPlayer(frame, player, "end_crystal", 32.0);
        int nearbyTntMinecartCount = countNearbyEntityTypeAroundPlayer(frame, player, "tnt_minecart", 28.0)
            + countNearbyEntityTypeAroundPlayer(frame, player, "tnt", 28.0);
        if (warden != null) {
            nearbyEndCrystalCount += countNearbyEntityType(frame, warden, "end_crystal", 32.0);
            nearbyTntMinecartCount += countNearbyEntityType(frame, warden, "tnt_minecart", 28.0);
            nearbyTntMinecartCount += countNearbyEntityType(frame, warden, "tnt", 28.0);
        } else if (this.bossTracker.lastWardenSeenTick() >= 0 && this.tickSource.getAsLong() - this.bossTracker.lastWardenSeenTick() <= 200) {
            nearbyEndCrystalCount += countNearbyEntityTypeAroundPoint(
                frame,
                this.bossTracker.lastWardenSeenX(),
                this.bossTracker.lastWardenSeenY(),
                this.bossTracker.lastWardenSeenZ(),
                "end_crystal",
                32.0
            );
            nearbyTntMinecartCount += countNearbyEntityTypeAroundPoint(
                frame,
                this.bossTracker.lastWardenSeenX(),
                this.bossTracker.lastWardenSeenY(),
                this.bossTracker.lastWardenSeenZ(),
                "tnt_minecart",
                28.0
            );
            nearbyTntMinecartCount += countNearbyEntityTypeAroundPoint(
                frame,
                this.bossTracker.lastWardenSeenX(),
                this.bossTracker.lastWardenSeenY(),
                this.bossTracker.lastWardenSeenZ(),
                "tnt",
                28.0
            );
        }

        if (nearbyEndCrystalCount > 0) {
            this.bossTracker.recordEndCrystalSetup(this.tickSource.getAsLong());
        }
        if (nearbyTntMinecartCount > 0) {
            this.bossTracker.recordTntSetup(this.tickSource.getAsLong());
        }
    }

    private ThreatObservation nearestThreatOfType(List<ThreatObservation> threats, String hostileType) {
        for (ThreatObservation threat : threats) {
            if (hostileType.equals(threat.type())) {
                return threat;
            }
        }
        return null;
    }

    private int countNearbyEntityType(WorldObservationFrame frame, ThreatObservation origin, String type, double radius) {
        return countNearbyEntityTypeAroundPoint(frame, origin.x(), origin.y(), origin.z(), type, radius);
    }

    private int countNearbyEntityTypeAroundPlayer(WorldObservationFrame frame, ClientPlayerEntity player,
            String type, double radius) {
        return frame.countEntities(player.getBoundingBox().expand(radius, radius, radius), null, 0, type);
    }

    private int countNearbyEntityTypeAroundPoint(WorldObservationFrame frame, double x, double y, double z,
            String type, double radius) {
        return frame.countEntities(new Box(x-radius, y-radius, z-radius, x+radius, y+radius, z+radius),
            new Vec3d(x, y, z), radius, type);
    }

    public void observeTrackedDragonDefeat(ClientWorld world) {
        if (this.bossTracker.trackedDragonEntityId() < 0) {
            return;
        }
        Entity entity = world.getEntityById(this.bossTracker.trackedDragonEntityId());
        if (!(entity instanceof LivingEntity living)) {
            return;
        }
        // 死亡アニメーション（DYINGフェーズ）開始時点で討伐とみなす
        boolean dyingPhase = entity instanceof EnderDragonEntity dragon
            && dragon.getPhaseManager().getCurrent().getType() == PhaseType.DYING;
        if (living.isDead() || living.getHealth() <= 0.0f || living.deathTime > 0 || dyingPhase) {
            this.bossTracker.recordDragonDefeat(this.tickSource.getAsLong());
        }
    }

    private boolean isRecentDragonDefeatConfirmed() {
        if (this.bossTracker.lastDragonSeenTick() < 0 || this.tickSource.getAsLong() - this.bossTracker.lastDragonSeenTick() > DRAGON_DEFEAT_WINDOW_TICKS) {
            return false;
        }
        if (this.bossTracker.lastDragonDefeatObservedTick() >= 0
            && this.tickSource.getAsLong() - this.bossTracker.lastDragonDefeatObservedTick() <= DRAGON_DEFEAT_WINDOW_TICKS) {
            return true;
        }
        return false;
    }

    private EnderDragonEntity findNearestDragon(ClientPlayerEntity player, WorldObservationFrame frame) {
        EnderDragonEntity nearest = null;
        double nearestDistance = DRAGON_SCAN_RADIUS;
        for (var sample : frame.entities(player.getBoundingBox().expand(DRAGON_SCAN_RADIUS))) {
            if (!(sample.entity() instanceof EnderDragonEntity entity)) continue;
            double distance = Math.sqrt(player.squaredDistanceTo(sample.position()));
            if (distance <= nearestDistance) {
                nearest = entity;
                nearestDistance = distance;
            }
        }
        return nearest;
    }

    private String dragonPhaseName(EnderDragonEntity dragon) {
        PhaseType<?> type = dragon.getPhaseManager().getCurrent().getType();
        if (type == PhaseType.STRAFE_PLAYER) {
            return "strafe_player";
        }
        if (type == PhaseType.LANDING_APPROACH) {
            return "landing_approach";
        }
        if (type == PhaseType.LANDING) {
            return "landing";
        }
        if (type == PhaseType.TAKEOFF) {
            return "takeoff";
        }
        if (type == PhaseType.SITTING_FLAMING) {
            return "sitting_flaming";
        }
        if (type == PhaseType.SITTING_SCANNING) {
            return "sitting_scanning";
        }
        if (type == PhaseType.SITTING_ATTACKING) {
            return "sitting_attacking";
        }
        if (type == PhaseType.CHARGING_PLAYER) {
            return "charging_player";
        }
        if (type == PhaseType.DYING) {
            return "dying";
        }
        if (type == PhaseType.HOVER) {
            return "hover";
        }
        return "holding_pattern";
    }

    private void appendDragonCombatInfo(JsonObject json, ClientPlayerEntity player, ClientWorld world, WorldObservationFrame frame) {
        String dimensionId = world.getRegistryKey().getValue().toString();
        boolean dragonPossible = "minecraft:the_end".equals(dimensionId)
            || (this.bossTracker.lastDragonSeenTick() >= 0 && this.tickSource.getAsLong() - this.bossTracker.lastDragonSeenTick() <= DRAGON_DEFEAT_WINDOW_TICKS);
        if (!dragonPossible) {
            return;
        }
        EnderDragonEntity dragon = findNearestDragon(player, frame);
        if (dragon != null) {
            this.bossTracker.observeDragon(this.tickSource.getAsLong(), dragon.getId());
            Vec3d dragonPosition = new Vec3d(dragon.getX(), dragon.getY(), dragon.getZ());
            json.addProperty("dragon_phase", dragonPhaseName(dragon));
            json.addProperty("dragon_distance", round(Math.sqrt(player.squaredDistanceTo(dragonPosition))));
            json.addProperty("dragon_horizontal", classifyHorizontal(player, dragonPosition));
            json.addProperty("dragon_vertical", classifyVertical(player, dragonPosition));
            json.addProperty(
                "end_crystal_count",
                countNearbyEntityTypeAroundPlayer(frame, player, "end_crystal", DRAGON_SCAN_RADIUS)
            );
        }
        if (this.bossTracker.lastDragonSeenTick() >= 0 && this.tickSource.getAsLong() - this.bossTracker.lastDragonSeenTick() <= DRAGON_DEFEAT_WINDOW_TICKS) {
            json.addProperty("dragon_defeat_confirmed", isRecentDragonDefeatConfirmed());
        }
    }

    public void observeTrackedWardenDefeat(ClientWorld world) {
        if (this.bossTracker.trackedWardenEntityId() < 0) {
            return;
        }
        Entity entity = world.getEntityById(this.bossTracker.trackedWardenEntityId());
        if (!(entity instanceof LivingEntity living)) {
            return;
        }
        // HP0 / isDead / 死亡アニメーション進行中を「討伐」とみなす。
        // 地面に潜って消える despawn は HP が残ったまま entity が消えるので、ここには引っかからない。
        if (living.isDead() || living.getHealth() <= 0.0f || living.deathTime > 0) {
            this.bossTracker.recordWardenDefeat(this.tickSource.getAsLong());
        }
    }

    private boolean isRecentWardenDefeatConfirmed(ClientWorld world) {
        if (this.bossTracker.lastWardenSeenTick() < 0 || this.tickSource.getAsLong() - this.bossTracker.lastWardenSeenTick() > 400) {
            return false;
        }
        // 追跡中の entity の死亡を直接観測したのが最優先（キル手段を問わず確実）
        if (this.bossTracker.lastWardenDefeatObservedTick() >= 0
            && this.tickSource.getAsLong() - this.bossTracker.lastWardenDefeatObservedTick() <= 400) {
            return true;
        }
        return false;
    }

    private boolean isWardenRangedTrapActive(ClientPlayerEntity player, ThreatObservation warden) {
        String heldItemId = Registries.ITEM.getId(player.getMainHandStack().getItem()).getPath();
        boolean rangedWeapon = "bow".equals(heldItemId) || "crossbow".equals(heldItemId);
        if (!rangedWeapon) {
            return false;
        }
        return player.getY() - warden.y() >= 5.0 && warden.distance() >= 8.0;
    }

    void appendCombatInfo(JsonObject json, ClientPlayerEntity player, ClientWorld world, WorldObservationFrame frame,
            List<ThreatObservation> threats) {
        ThreatObservation warden = nearestThreatOfType(threats, "warden");
        if (warden != null) {
            // ウォーデン周辺とプレイヤー周辺で同じ個体を二重に数えないよう max を取る
            int nearbyIronGolemCount = Math.max(
                countNearbyEntityType(frame, warden, "iron_golem", 20.0),
                countNearbyEntityTypeAroundPlayer(frame, player, "iron_golem", 20.0));
            int nearbyEndCrystalCount = Math.max(
                countNearbyEntityType(frame, warden, "end_crystal", 32.0),
                countNearbyEntityTypeAroundPlayer(frame, player, "end_crystal", 32.0));
            int nearbyTntMinecartCount = Math.max(
                countNearbyEntityType(frame, warden, "tnt_minecart", 28.0)
                    + countNearbyEntityType(frame, warden, "tnt", 28.0),
                countNearbyEntityTypeAroundPlayer(frame, player, "tnt_minecart", 28.0)
                    + countNearbyEntityTypeAroundPlayer(frame, player, "tnt", 28.0));
            long recentExplosionMs = ticksSince(this.tickSource.getAsLong(), this.lastExplosionTick.getAsLong());
            json.addProperty("warden_recently_hurt", warden.recentlyHurt());
            json.addProperty("warden_ranged_trap_active", isWardenRangedTrapActive(player, warden));
            json.addProperty("warden_nearby_iron_golem_count", nearbyIronGolemCount);
            json.addProperty(
                "warden_end_crystal_bombardment_active",
                recentExplosionMs <= 1500L
                    && (
                        nearbyEndCrystalCount > 0
                        || ticksSince(this.tickSource.getAsLong(), this.bossTracker.lastWardenEndCrystalObservedTick()) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
                    )
            );
            json.addProperty("warden_nearby_end_crystal_count", nearbyEndCrystalCount);
            json.addProperty(
                "warden_tnt_minecart_setup_active",
                nearbyTntMinecartCount > 0
                    || ticksSince(this.tickSource.getAsLong(), this.bossTracker.lastWardenTntSetupObservedTick()) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
            );
            json.addProperty("warden_nearby_tnt_minecart_count", nearbyTntMinecartCount);
        } else if (this.bossTracker.lastWardenSeenTick() >= 0 && this.tickSource.getAsLong() - this.bossTracker.lastWardenSeenTick() <= 200) {
            // 同上: プレイヤー周辺と最終目撃地点周辺の二重計上を避ける
            int nearbyIronGolemCount = Math.max(
                countNearbyEntityTypeAroundPlayer(frame, player, "iron_golem", 20.0),
                countNearbyEntityTypeAroundPoint(frame, this.bossTracker.lastWardenSeenX(), this.bossTracker.lastWardenSeenY(), this.bossTracker.lastWardenSeenZ(), "iron_golem", 20.0));
            int nearbyEndCrystalCount = Math.max(
                countNearbyEntityTypeAroundPlayer(frame, player, "end_crystal", 32.0),
                countNearbyEntityTypeAroundPoint(frame, this.bossTracker.lastWardenSeenX(), this.bossTracker.lastWardenSeenY(), this.bossTracker.lastWardenSeenZ(), "end_crystal", 32.0));
            int nearbyTntMinecartCount = Math.max(
                countNearbyEntityTypeAroundPlayer(frame, player, "tnt_minecart", 28.0)
                    + countNearbyEntityTypeAroundPlayer(frame, player, "tnt", 28.0),
                countNearbyEntityTypeAroundPoint(frame, this.bossTracker.lastWardenSeenX(), this.bossTracker.lastWardenSeenY(), this.bossTracker.lastWardenSeenZ(), "tnt_minecart", 28.0)
                    + countNearbyEntityTypeAroundPoint(frame, this.bossTracker.lastWardenSeenX(), this.bossTracker.lastWardenSeenY(), this.bossTracker.lastWardenSeenZ(), "tnt", 28.0));
            long recentExplosionMs = ticksSince(this.tickSource.getAsLong(), this.lastExplosionTick.getAsLong());
            json.addProperty("warden_nearby_iron_golem_count", nearbyIronGolemCount);
            json.addProperty(
                "warden_end_crystal_bombardment_active",
                recentExplosionMs <= 1500L
                    && (
                        nearbyEndCrystalCount > 0
                        || ticksSince(this.tickSource.getAsLong(), this.bossTracker.lastWardenEndCrystalObservedTick()) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
                    )
            );
            json.addProperty("warden_nearby_end_crystal_count", nearbyEndCrystalCount);
            json.addProperty(
                "warden_tnt_minecart_setup_active",
                nearbyTntMinecartCount > 0
                    || ticksSince(this.tickSource.getAsLong(), this.bossTracker.lastWardenTntSetupObservedTick()) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
            );
            json.addProperty("warden_nearby_tnt_minecart_count", nearbyTntMinecartCount);
            // 討伐直後（姿が消えてから10秒以内）の combat_ended でも
            // 討伐確認が届くようにここでも送る
            json.addProperty("warden_defeat_confirmed", isRecentWardenDefeatConfirmed(world));
        } else {
            json.addProperty("warden_defeat_confirmed", isRecentWardenDefeatConfirmed(world));
        }
        appendDragonCombatInfo(json, player, world, frame);
    }

}
