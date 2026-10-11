package dogido.fabric.horse_recall;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.Predicate;

/** 周囲2〜5ブロック、上下1ブロックまでの着地点を近い順に選ぶ。ワールド照会は呼出側が所有する。 */
public final class HorseRecallLanding {
    private HorseRecallLanding() {}

    /** x,zは2×2床の中央、yは馬の足元。四隅の床はx-1..x,z-1..z,y-1にある。 */
    public record Point(int x, int y, int z) {
        public double horizontalDistanceSquared(double playerX, double playerZ) {
            return (x - playerX) * (x - playerX) + (z - playerZ) * (z - playerZ);
        }
        public boolean near(double playerX, double playerY, double playerZ) {
            double distance = horizontalDistanceSquared(playerX, playerZ);
            return distance >= 4.0 && distance <= 25.0 && Math.abs(y - playerY) <= 1.5;
        }
    }

    public static Point find(double x, double y, double z, Predicate<Point> safe) {
        List<Point> candidates = new ArrayList<>();
        for (int dx = -5; dx <= 5; dx++) {
            for (int dz = -5; dz <= 5; dz++) {
                for (int dy : new int[] {0, 1, -1}) {
                    Point point = new Point((int)Math.floor(x) + dx, (int)Math.floor(y) + dy, (int)Math.floor(z) + dz);
                    if (point.near(x, y, z)) candidates.add(point);
                }
            }
        }
        candidates.sort(Comparator.comparingDouble((Point p) -> p.horizontalDistanceSquared(x, z))
            .thenComparingDouble(p -> Math.abs(p.y() - y)));
        return candidates.stream().filter(safe).findFirst().orElse(null);
    }
}
