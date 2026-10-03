package dogido.fabric;

/** Independently timed authored layers. No mirroring, morphing or generated in-betweens. */
final class ThinkingAnimation {
    static final int WIDTH = 1210, HEIGHT = 1400;
    static final int BODY_STEP_MS = 960;
    static final double FLOAT_PERIOD_SECONDS = 7.6;
    private static final int[] BODY_SEQUENCE = {1, 2, 3, 4, 5, 4, 3, 2};
    record Frame(int body, int tail, String eyes) { }

    static Frame frame(long elapsedMs, boolean motion) {
        long t = motion ? Math.max(0, elapsedMs) : 0;
        long gaze = t % 21600;
        return new Frame(BODY_SEQUENCE[(int) (t / BODY_STEP_MS % BODY_SEQUENCE.length)],
            (int) ((t % 6600 + 1050) / 3300 % 2) + 1,
            gaze < 5400 || (gaze >= 9600 && gaze < 16800) ? "r" : "l");
    }

    static String[] layers(Frame frame) {
        return new String[]{"thinking_tail_" + frame.tail() + ".png",
            "thinking_body_" + frame.body() + ".png", "thinking_face.png",
            "thinking_eye_" + frame.eyes() + ".png"};
    }
}
