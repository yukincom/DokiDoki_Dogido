package dogido.fabric.observation;



public final class ObservationTime {
    private ObservationTime() {}

    public static long ticksSince(long tickCounter, long tick) {
        return Math.max(0L, (tickCounter - tick) * 50L);
    }
}
