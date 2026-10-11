package dogido.lab;

/** Blockbench hand keys, in seconds/degrees; the final pose closes the loop. */
public final class IdleMotion {
    public static final float LENGTH = 1.6667F;
    private static final float[] TIMES = {0, .25F, .5F, .625F, .75F, 1, 1.5F, LENGTH};
    private static final float[][] RIGHT = {
        {0,0,0}, {0,0,0}, {0,0,20}, {0,0,32.5F},
        {0,0,20}, {0,0,10}, {0,0,-20}, {0,0,0}
    };
    private static final float[][] LEFT = {
        {0,0,0}, {0,0,0}, {0,0,-20}, {0,0,-32.5F},
        {-2.3018F,3.2987F,-19.7222F}, {0,0,-10}, {0,0,20}, {0,0,0}
    };

    private IdleMotion() {}

    public static float angle(boolean left, int axis, float seconds) {
        float t = ((seconds % LENGTH) + LENGTH) % LENGTH;
        float[][] keys = left ? LEFT : RIGHT;
        for (int i = 1; i < TIMES.length; i++) {
            if (t <= TIMES[i]) {
                float weight = (t - TIMES[i - 1]) / (TIMES[i] - TIMES[i - 1]);
                return (keys[i - 1][axis] + weight * (keys[i][axis] - keys[i - 1][axis]))
                    * (float) Math.PI / 180F;
            }
        }
        return 0;
    }

    public static double hoverStep(int tick) {
        // 4-second cycle, +/- 0.06 blocks. The sum over a cycle is zero.
        double phase = Math.floorMod(tick, 80) * Math.PI * 2 / 80;
        return .06 * (Math.sin(phase) - Math.sin(phase - Math.PI * 2 / 80));
    }
}
