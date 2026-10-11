package dogido.fabric.observation;



public final class ObservationNumbers {
    private ObservationNumbers() {}

    public static double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }

    public static double clamp01(double value) {
        return Math.max(0.0, Math.min(1.0, value));
    }
}
