package dogido.fabric.horse_recall;

/** 粒子の寿命と明滅の純粋な計算。最終tickで透明にし、寿命後に不透明へ戻さない。 */
public final class HorseRecallVisuals {
    private HorseRecallVisuals() {}

    public static float alpha(int age, int maxAge, double phase, boolean burst) {
        if (age < 0 || maxAge <= 0 || age >= maxAge) return 0.0F;
        double progress = (double)age / maxAge;
        double fade = Math.min(1.0, (age + 1) / 3.0) * (1.0 - progress);
        double flicker = burst ? 0.2 + 0.8 * Math.pow(Math.max(0, Math.sin(age * 1.5 + phase)), 2) : 0.85;
        return (float)(fade * flicker);
    }
}
