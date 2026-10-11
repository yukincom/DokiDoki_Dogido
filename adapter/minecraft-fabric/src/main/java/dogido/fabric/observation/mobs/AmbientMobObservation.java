package dogido.fabric.observation.mobs;

import dogido.fabric.observation.MobEnvironmentObservation;
import dogido.fabric.observation.MobIdentityObservation;
import java.util.UUID;

public record AmbientMobObservation(
        UUID uuid,
        String type,
        double distance,
        String horizontalDirection,
        String verticalRelation,
        String temperament,
        String cautionReason,
        Boolean isBaby,
        String profession,
        String villagerType,
        MobIdentityObservation identity,
        MobEnvironmentObservation environment
    ) {
    }
