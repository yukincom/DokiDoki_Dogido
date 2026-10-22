package dogido.lab;

/** 手の既存キーと身体の穏やかな上下波形を返す。入力は秒／tick、出力はラジアン／blockで、実移動と安全検査は身体側が担当する。 */
public final class IdleMotion {
    public static final double HOVER_AMPLITUDE = .18;
    public static final int HOVER_PERIOD_TICKS = 120;
    /** 実際に加えられた浮遊だけ位相を進める。危険・衝突で一部を捨てて高度が累積するのを防ぐ。 */
    public static final class FloatCycle {
        private int tick;
        public double offset() { return hoverOffset(tick); }
        public double nextStep() { return hoverStep(tick+1); }
        public void advance() { tick = (tick+1)%HOVER_PERIOD_TICKS; }
        public void reset() { tick = 0; }
    }
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

    /** 6秒周期・振幅0.18block。上下の端では速度が自然に0へ近づき、折り返しで跳ねない。 */
    public static double hoverOffset(int tick) {
        return HOVER_AMPLITUDE * Math.sin(Math.floorMod(tick,HOVER_PERIOD_TICKS) * Math.PI * 2 / HOVER_PERIOD_TICKS);
    }
    /** このtickの上下移動量。周期の和は0で、待機を続けても高度を積み上げない。 */
    public static double hoverStep(int tick) { return hoverOffset(tick)-hoverOffset(tick-1); }
}
