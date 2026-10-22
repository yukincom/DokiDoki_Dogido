package dogido.lab.control;

import java.util.ArrayList;
import java.util.List;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/**
 * FOLLOWの敵位置・速度から作る一tick分の危険領域。ワールド探索や移動は呼出し側が所有する。
 * 敵とownerを結ぶ有限の射線帯と敵の短期予測経路を、目標・移動区間・ワープ着地点で共有する。
 */
public final class BodySafety {
    public record Enemy(Box bounds, Vec3d velocity) {}
    public record Corridor(Vec3d start, Vec3d end, double radius) {}
    public static final int PREDICTION_TICKS = 10;
    public static final double SCAN_RADIUS = 32;
    private static final double EPS = 1e-8;
    // 外接球で身体の角まで含める。斜めの射線を巨大な軸平行boxへ広げない。
    private static final double BODY_RADIUS = Math.sqrt(3) * BodyTuning.BODY_SIZE / 2;
    private final List<Corridor> corridors;
    public static final BodySafety EMPTY = new BodySafety(List.of());

    public BodySafety(List<Corridor> corridors) { this.corridors = List.copyOf(corridors); }
    public boolean isEmpty() { return corridors.isEmpty(); }
    public static BodySafety capture(Vec3d ownerCenter, List<Enemy> enemies) {
        List<Corridor> regions = new ArrayList<>();
        for (Enemy enemy : enemies) {
            Vec3d center = enemy.bounds().getCenter();
            double radius = Math.sqrt(Math.pow(enemy.bounds().getLengthX(),2)
                + Math.pow(enemy.bounds().getLengthY(),2) + Math.pow(enemy.bounds().getLengthZ(),2)) / 2;
            regions.add(new Corridor(center,ownerCenter,.45));
            // 速度はその瞬間の観測値。巨大な予測値は走査範囲を越えない長さへ制限する。
            Vec3d prediction = enemy.velocity().multiply(PREDICTION_TICKS);
            if (prediction.length() > SCAN_RADIUS / 2) prediction = prediction.normalize().multiply(SCAN_RADIUS / 2);
            regions.add(new Corridor(center,center.add(prediction),radius+.6));
        }
        return new BodySafety(regions);
    }
    private static Vec3d center(Vec3d feet) { return feet.add(0,BodyTuning.BODY_SIZE/2,0); }
    public boolean safeTarget(Vec3d feet) {
        Vec3d point = center(feet);
        return corridors.stream().noneMatch(c -> distance(point,c.start(),c.end()) < c.radius()+BODY_RADIUS-EPS);
    }
    public boolean threatened(Vec3d feet) { return !safeTarget(feet); }

    /**
     * 安全な場所から危険帯へ入ることを禁止する。敵が動いて既に重なった場合だけ、
     * 全領域で重なりを増やさず、少なくとも一領域から離れる移動を許す。
     */
    public boolean allowsStep(Vec3d feet, Vec3d delta) {
        Vec3d start = center(feet), end = start.add(delta);
        boolean overlapping = false, improving = false;
        for (Corridor c : corridors) {
            double radius = c.radius()+BODY_RADIUS;
            double before = distance(start,c.start(),c.end());
            double closest = segmentDistance(start,end,c.start(),c.end());
            if (before < radius-EPS) {
                overlapping = true;
                if (closest < before-EPS) return false;
                if (distance(end,c.start(),c.end()) > before+EPS) improving = true;
            } else if (closest < radius-EPS) return false;
        }
        return !overlapping || improving;
    }
    private static double distance(Vec3d point, Vec3d start, Vec3d end) {
        Vec3d axis = end.subtract(start);
        double t = axis.lengthSquared() < EPS ? 0 : clamp(point.subtract(start).dotProduct(axis)/axis.lengthSquared());
        return point.distanceTo(start.add(axis.multiply(t)));
    }
    private static double clamp(double value) { return Math.max(0,Math.min(1,value)); }

    /** 退化した線分と平行線分も扱う、有限線分同士の最短距離。 */
    private static double segmentDistance(Vec3d a, Vec3d b, Vec3d c, Vec3d d) {
        Vec3d u = b.subtract(a), v = d.subtract(c), w = a.subtract(c);
        double aa = u.lengthSquared(), bb = u.dotProduct(v), cc = v.lengthSquared();
        double dd = u.dotProduct(w), ee = v.dotProduct(w);
        if (aa < EPS) return distance(a,c,d);
        if (cc < EPS) return distance(c,a,b);
        double denominator = aa*cc-bb*bb;
        double s = denominator > EPS ? clamp((bb*ee-cc*dd)/denominator) : 0;
        double t = (bb*s+ee)/cc;
        if (t < 0) { t = 0; s = clamp(-dd/aa); }
        else if (t > 1) { t = 1; s = clamp((bb-dd)/aa); }
        return a.add(u.multiply(s)).distanceTo(c.add(v.multiply(t)));
    }
}
