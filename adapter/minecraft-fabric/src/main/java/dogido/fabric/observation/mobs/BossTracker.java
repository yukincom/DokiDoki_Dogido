package dogido.fabric.observation.mobs;

/** One owner for boss tracking shared by threat scans, outcome records and boss observations. */
public final class BossTracker {
    private long lastWardenSeenTick = -1000;
    private int trackedWardenEntityId = -1;
    private long lastWardenDefeatObservedTick = -1000;
    private long lastDragonSeenTick = -1000;
    private int trackedDragonEntityId = -1;
    private long lastDragonDefeatObservedTick = -1000;
    private long lastWardenEndCrystalObservedTick = -1000;
    private long lastWardenTntSetupObservedTick = -1000;
    private double lastWardenSeenX = 0.0;
    private double lastWardenSeenY = 0.0;
    private double lastWardenSeenZ = 0.0;

    void observeWarden(long tick, double x, double y, double z, int entityId) {
        this.lastWardenSeenTick = tick;
        this.lastWardenSeenX = x;
        this.lastWardenSeenY = y;
        this.lastWardenSeenZ = z;
        this.trackedWardenEntityId = entityId;
    }
    void observeDragon(long tick, int entityId) {
        this.lastDragonSeenTick = tick;
        this.trackedDragonEntityId = entityId;
    }
    void recordWardenDefeat(long tick) {
        this.lastWardenDefeatObservedTick = tick;
        this.trackedWardenEntityId = -1;
    }
    void recordDragonDefeat(long tick) {
        this.lastDragonDefeatObservedTick = tick;
        this.trackedDragonEntityId = -1;
    }
    void recordEndCrystalSetup(long tick) { this.lastWardenEndCrystalObservedTick = tick; }
    void recordTntSetup(long tick) { this.lastWardenTntSetupObservedTick = tick; }
    public void reset() {
        this.lastWardenSeenTick = -1000;
        this.trackedWardenEntityId = -1;
        this.lastWardenDefeatObservedTick = -1000;
        this.lastDragonSeenTick = -1000;
        this.trackedDragonEntityId = -1;
        this.lastDragonDefeatObservedTick = -1000;
        this.lastWardenEndCrystalObservedTick = -1000;
        this.lastWardenTntSetupObservedTick = -1000;
    }

    long lastWardenSeenTick() { return this.lastWardenSeenTick; }
    int trackedWardenEntityId() { return this.trackedWardenEntityId; }
    long lastWardenDefeatObservedTick() { return this.lastWardenDefeatObservedTick; }
    long lastDragonSeenTick() { return this.lastDragonSeenTick; }
    int trackedDragonEntityId() { return this.trackedDragonEntityId; }
    long lastDragonDefeatObservedTick() { return this.lastDragonDefeatObservedTick; }
    long lastWardenEndCrystalObservedTick() { return this.lastWardenEndCrystalObservedTick; }
    long lastWardenTntSetupObservedTick() { return this.lastWardenTntSetupObservedTick; }
    double lastWardenSeenX() { return this.lastWardenSeenX; }
    double lastWardenSeenY() { return this.lastWardenSeenY; }
    double lastWardenSeenZ() { return this.lastWardenSeenZ; }
}
