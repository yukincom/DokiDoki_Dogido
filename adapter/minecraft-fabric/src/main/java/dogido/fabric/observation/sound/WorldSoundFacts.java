package dogido.fabric.observation.sound;

/** Sound timestamps only; world capture does not own or renew them. */
public record WorldSoundFacts(String ominousKind, long ominousTick, long rainTick,
        long thunderTick, long enderEyeTick) {
    public static final int OMINOUS_SOUND_TTL_TICKS = 80;
    public static final int WEATHER_SOUND_TTL_TICKS = 80;
    public static final int ENDER_EYE_LAUNCH_TTL_TICKS = 40;
}
