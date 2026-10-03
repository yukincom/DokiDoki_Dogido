package dogido.fabric;

import java.util.Map;
import java.util.Objects;

/** Distinguishes a sampled block change from entering an existing portal's range. */
final class PortalObservationTracker {
    private Map<Long, String> previous = Map.of();
    private long previousTick = -1;
    private String activeType;
    private Long anchor;
    private String encounter;

    String observe(Map<Long, String> loadedBlocks, Map<Long, String> nearbyPortals, Long nearestPosition,
                   boolean inFrontAndVisible, long tick, long maxGapTicks) {
        if (previousTick < 0 || tick < previousTick || tick - previousTick > maxGapTicks) {
            reset();
        }
        String nearestType = nearestPosition == null ? null : nearbyPortals.get(nearestPosition);
        if (nearestType == null) {
            activeType = null;
            anchor = null;
            encounter = null;
        } else if (!Objects.equals(activeType, nearestType)
                   || anchor == null || !Objects.equals(nearbyPortals.get(anchor), activeType)) {
            // An absent map key means unobserved/unloaded, never proof of an empty block.
            boolean appeared = "".equals(previous.get(nearestPosition));
            encounter = appeared ? (inFrontAndVisible ? "appeared" : "observed") : "arrived";
            activeType = nearestType;
            anchor = nearestPosition;
        }
        previous = Map.copyOf(loadedBlocks);
        previousTick = tick;
        // Retain an audio/threat-tick detection until the later status snapshot.
        return encounter;
    }

    void reset() {
        previous = Map.of();
        previousTick = -1;
        activeType = null;
        anchor = null;
        encounter = null;
    }
}
