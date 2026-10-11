package dogido.fabric.observation.mobs;

import dogido.fabric.observation.MobEnvironmentObservation;
import dogido.fabric.observation.MobIdentityObservation;
import java.util.UUID;

public record ThreatObservation(
        UUID uuid,
        String type,
        double distance,
        String horizontalDirection,
        String cardinalDirection,
        String verticalRelation,
        boolean approaching,
        boolean isRearThreat,
        boolean lineOfSight,
        boolean onFire,
        boolean inWater,
        double x,
        double y,
        double z,
        float health,
        boolean recentlyHurt,
        boolean fuseActive,
        MobIdentityObservation identity,
        MobEnvironmentObservation environment
    ) {
    }
