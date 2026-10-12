package dogido.fabric.horse_recall;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.Predicate;

/** 周囲2〜5ブロック、上下1ブロックまでの着地点を近い順に選ぶ。ワールド照会は呼出側が所有する。 */
public final class HorseRecallLanding {
    private HorseRecallLanding() {}

    /** x,zは着地中心、yは足元。水生はこの高さの横3×3から水域を検査する。 */
    public record Point(int x, int y, int z) {
        public double horizontalDistanceSquared(double playerX, double playerZ) {
            return (x - playerX) * (x - playerX) + (z - playerZ) * (z - playerZ);
        }
        public boolean near(double playerX, double playerY, double playerZ) {
            double distance = horizontalDistanceSquared(playerX, playerZ);
            return distance >= 4.0 && distance <= 25.0 && Math.abs(y - playerY) <= 1.5;
        }
    }

    public record Cell(int x, int y, int z) {}

    /** 閉区間の検査範囲。全チャンク確認を先に終え、その後で同じ範囲のblockを読む。 */
    public record Volume(int minX, int minY, int minZ, int maxX, int maxY, int maxZ) {
        public boolean all(Predicate<Cell> check) {
            for (int x = minX; x <= maxX; x++) for (int z = minZ; z <= maxZ; z++)
                for (int y = minY; y <= maxY; y++) if (!check.test(new Cell(x, y, z))) return false;
            return true;
        }
    }

    /** 水生は横3×3と身体の高さ分の水。陸上は最低2×2×3、飛行は実寸の空間を確保する。 */
    public static Volume volume(Point point, HorseRecallChecks.Kind kind, double width, double height) {
        if (kind.aquatic()) return new Volume(Math.min(point.x() - 1, (int)Math.floor(point.x() - width / 2)), point.y(),
            Math.min(point.z() - 1, (int)Math.floor(point.z() - width / 2)),
            Math.max(point.x() + 1, (int)Math.ceil(point.x() + width / 2) - 1), point.y() + (int)Math.ceil(height) - 1,
            Math.max(point.z() + 1, (int)Math.ceil(point.z() + width / 2) - 1));
        double clearanceWidth = kind.flying() ? width : Math.max(2, width);
        int clearanceHeight = (int)Math.ceil(kind.flying() ? height : Math.max(3, height));
        return new Volume((int)Math.floor(point.x() - clearanceWidth / 2), point.y(),
            (int)Math.floor(point.z() - clearanceWidth / 2),
            (int)Math.ceil(point.x() + clearanceWidth / 2) - 1, point.y() + clearanceHeight - 1,
            (int)Math.ceil(point.z() + clearanceWidth / 2) - 1);
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
