package dogido.fabric.observation.mobs;

import java.util.UUID;

public record HostileOutcomeObservation(
        UUID entityUuid,
        String type,
        String outcome,
        String evidence,
        long observedTick
    ) {
    }
