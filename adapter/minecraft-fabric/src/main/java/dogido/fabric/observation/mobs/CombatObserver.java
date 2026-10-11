package dogido.fabric.observation.mobs;

import com.google.gson.JsonObject;
import dogido.fabric.observation.mobs.ThreatObservation;
import dogido.fabric.observation.mobs.ThreatObserver;
import dogido.fabric.observation.sound.AudioThreatObservation;
import dogido.fabric.observation.sound.SoundObserver;
import java.util.List;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;

import static dogido.fabric.observation.MobIdentityObservation.entityTypeName;
import static dogido.fabric.observation.ObservationNumbers.round;
import static dogido.fabric.observation.ObservationTime.ticksSince;
import static dogido.fabric.protocol.EventObservationJson.buildHostileOutcomes;

public final class CombatObserver {
    private final ThreatObserver threatObserver;
    private final SoundObserver soundObserver;
    private final BossObserver bossObserver;
    private final double maxThreatDistance;
    private final double visibleThreatDistance;
    private final java.util.function.LongSupplier tickSource;

    public CombatObserver(ThreatObserver threatObserver, SoundObserver soundObserver, BossObserver bossObserver,
            double maxThreatDistance, double visibleThreatDistance, java.util.function.LongSupplier tickSource) {
        this.threatObserver = threatObserver;
        this.soundObserver = soundObserver;
        this.bossObserver = bossObserver;
        this.maxThreatDistance = maxThreatDistance;
        this.visibleThreatDistance = visibleThreatDistance;
        this.tickSource = tickSource;
    }

    public JsonObject buildCombat(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonObject json = new JsonObject();
        json.addProperty("recent_damage_ms", ticksSince(this.tickSource.getAsLong(), this.threatObserver.lastDamageTick()));
        json.addProperty("recent_hostile_visual_ms", ticksSince(this.tickSource.getAsLong(), this.threatObserver.lastVisualObservedTick()));
        json.addProperty("recent_hostile_audio_ms", ticksSince(this.tickSource.getAsLong(), this.soundObserver.lastThreatObservedTick()));
        json.addProperty("hostiles_within_7", countThreatsWithin(threats, 7.0));
        json.addProperty("hostiles_within_10", countThreatsWithin(threats, 10.0));
        double scanDistance = Math.max(this.maxThreatDistance, this.visibleThreatDistance);
        json.addProperty("hostile_scan_distance", round(scanDistance));
        json.addProperty("hostiles_within_scan_ground", countGroundThreatsWithin(threats, scanDistance));
        json.addProperty("combat_active_hint", this.threatObserver.combatActive() || !threats.isEmpty() || !audioThreats.isEmpty());
        int nearbyExperienceOrbCount = countNearbyExperienceOrbs(world, player);
        json.addProperty("nearby_experience_orb_count", nearbyExperienceOrbCount);
        json.add("hostile_outcomes", buildHostileOutcomes(this.threatObserver.outcomes()));
        this.bossObserver.appendCombatInfo(json, player, world, threats);
        return json;
    }

    private int countThreatsWithin(List<ThreatObservation> threats, double distance) {
        int count = 0;
        for (ThreatObservation threat : threats) {
            if (threat.distance() <= distance) {
                count += 1;
            }
        }
        return count;
    }

    private int countGroundThreatsWithin(List<ThreatObservation> threats, double distance) {
        int count = 0;
        for (ThreatObservation threat : threats) {
            if (threat.distance() > distance || isFlyingThreatType(threat.type())) {
                continue;
            }
            count += 1;
        }
        return count;
    }

    private int countNearbyExperienceOrbs(ClientWorld world, ClientPlayerEntity player) {
        int count = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            player.getBoundingBox().expand(24.0, 12.0, 24.0)
        )) {
            if ("experience_orb".equals(entityTypeName(entity))) {
                count += 1;
            }
        }
        return count;
    }

    private boolean isFlyingThreatType(String hostileType) {
        return "blaze".equals(hostileType)
            || "ender_dragon".equals(hostileType)
            || "ghast".equals(hostileType)
            || "phantom".equals(hostileType)
            || "vex".equals(hostileType)
            || "wither".equals(hostileType);
    }
}
