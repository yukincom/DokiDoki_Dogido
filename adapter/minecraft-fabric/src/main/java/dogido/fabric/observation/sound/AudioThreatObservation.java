package dogido.fabric.observation.sound;

import dogido.fabric.observation.MobIdentityObservation;

public record AudioThreatObservation(
        String label,
        String sourceId,
        String soundEvent,
        String horizontalDirection,
        String verticalRelation,
        String distanceBand,
        String certainty,
        boolean spokenNameAllowed,
        long observedTick,
        long heardAgoMs,
        MobIdentityObservation identity
    ) {
    }
