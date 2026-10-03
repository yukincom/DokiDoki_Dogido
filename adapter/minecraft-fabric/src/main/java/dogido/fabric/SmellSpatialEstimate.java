package dogido.fabric;

/** Coarse bearing of the resolved winning source; no range estimate. */
final class SmellSpatialEstimate {
    private static final String[] CARDINALS = {
        "north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"
    };

    record Point(double x, double y, double z) {
        double squaredDistance(Point other) {
            return square(x - other.x) + square(y - other.y) + square(z - other.z);
        }

        boolean finite() {
            return Double.isFinite(x) && Double.isFinite(y) && Double.isFinite(z);
        }
    }

    /** Block identities/positions stay private; strength is refreshed between scans. */
    record LocatedCandidate(SmellPolicy.Candidate candidate, Point position) {
        SmellPolicy.Candidate observedFrom(Point observer) {
            return candidate.atDistance(Math.sqrt(position.squaredDistance(observer)));
        }
    }

    record Estimate(String cardinal, String vertical) {}

    static Estimate resolve(SmellPolicy.Observation observation, Point observer, Point sourcePosition) {
        SmellPolicy.ResolvedSource source = observation.resolvedSource();
        if (source == null) return null;
        if (observer == null || sourcePosition == null || !observer.finite() || !sourcePosition.finite()) {
            return null;
        }
        double dx = sourcePosition.x - observer.x;
        double dy = sourcePosition.y - observer.y;
        double dz = sourcePosition.z - observer.z;
        // A source directly above/below has no horizontal bearing.
        String cardinal = null;
        if (Math.hypot(dx, dz) > 1e-8) {
            int octant = Math.floorMod((int) Math.floor(Math.atan2(dx, -dz) / (Math.PI / 4) + 0.5), 8);
            cardinal = CARDINALS[octant];
        }
        String vertical = dy > 1.0 ? "above" : dy < -1.0 ? "below" : null;
        return cardinal == null && vertical == null ? null : new Estimate(cardinal, vertical);
    }

    private static double square(double value) { return value * value; }
}
