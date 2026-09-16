package dogido.fabric;

/** Smooth outer-silhouette movement in authored image coordinates. No image edits. */
final class ThinkingMesh {
    static final int WIDTH = 1210, HEIGHT = 1249;
    static final int COLUMNS = 40, ROWS = 42;
    static final double PERIOD_MS = 5200;
    static final float MAX_OFFSET = 32;
    // Same protected interior as the approved browser preview (face AND both hands).
    private static final float[][] INTERIOR = {
        {310,70}, {815,80}, {885,275}, {935,445}, {940,890},
        {130,900}, {110,590}, {180,400}, {265,275}
    };
    private static final float[][] BUBBLES = {{880,1000},{1230,1000},{1230,1270},{880,1270}};
    record Point(float x, float y, float u, float v) { }

    static float weight(float x, float y) {
        double distance = Math.min(distanceOutside(x, y, INTERIOR), distanceOutside(x, y, BUBBLES));
        // More than a grid-cell diagonal: cells touching protected art are entirely rigid.
        double t = Math.clamp((distance - 48) / 90, 0, 1);
        return (float) (t * t * (3 - 2 * t));
    }

    private static double distanceOutside(float x, float y, float[][] polygon) {
        boolean inside = false;
        double distance = Double.POSITIVE_INFINITY;
        for (int i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
            double ax = polygon[j][0], ay = polygon[j][1], bx = polygon[i][0], by = polygon[i][1];
            if ((ay > y) != (by > y) && x < (bx - ax) * (y - ay) / (by - ay) + ax) inside = !inside;
            double dx = bx - ax, dy = by - ay;
            double t = Math.clamp(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0, 1);
            distance = Math.min(distance, Math.hypot(x - ax - t * dx, y - ay - t * dy));
        }
        return inside ? 0 : distance;
    }

    static Point point(int column, int row, long nowMs, boolean motion) {
        float u = column / (float) COLUMNS, v = row / (float) ROWS;
        float x = WIDTH * u, y = HEIGHT * v;
        double phase = (nowMs % (long) PERIOD_MS) / PERIOD_MS * Math.PI * 2;
        float amplitude = motion ? MAX_OFFSET * WEIGHTS[row * (COLUMNS + 1) + column] : 0;
        return new Point(x + amplitude * (float) Math.sin(phase + x * .009 + y * .003),
            y + amplitude * (float) Math.sin(phase + y * .008 - x * .004), u, v);
    }

    private static final float[] WEIGHTS = weights();
    private static float[] weights() {
        float[] values = new float[(COLUMNS + 1) * (ROWS + 1)];
        for (int row = 0; row <= ROWS; row++) for (int col = 0; col <= COLUMNS; col++)
            values[row * (COLUMNS + 1) + col] = weight(WIDTH * col / (float) COLUMNS, HEIGHT * row / (float) ROWS);
        return values;
    }
}
