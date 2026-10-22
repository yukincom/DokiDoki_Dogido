package dogido.lab.control;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/** 身体の位置・移動量・障害物から衝突を抜けない移動量を計算する純粋な幾何処理。DIRECTは生物のみ、旧試験制御は危険物も障害物として渡し、行動先の選択は呼び出し側が所有する。 */
public final class BodyMotion {
    private static final double EPS = 1e-7;
    private BodyMotion() {}

    public static Box box(Vec3d feet) {
        double half = BodyTuning.BODY_SIZE / 2;
        return new Box(feet.x-half, feet.y, feet.z-half, feet.x+half, feet.y+BodyTuning.BODY_SIZE, feet.z+half);
    }
    public static Vec3d forward(float yaw) {
        double angle = Math.toRadians(yaw);
        return new Vec3d(-Math.sin(angle), 0, Math.cos(angle));
    }
    public static Vec3d offset(Vec3d owner, float yaw, double front, double right, double height) {
        Vec3d f = forward(yaw);
        return owner.add(f.multiply(front)).add(-f.z * right, height, f.x * right);
    }

    /** Earliest AABB contact; returns 1 when an entire step is clear. */
    public static double contact(Box a, Vec3d step, Box b) {
        if (a.intersects(b)) return 0;
        double enter = Double.NEGATIVE_INFINITY, leave = Double.POSITIVE_INFINITY;
        double[] lo = {a.minX,a.minY,a.minZ}, hi = {a.maxX,a.maxY,a.maxZ};
        double[] blo = {b.minX,b.minY,b.minZ}, bhi = {b.maxX,b.maxY,b.maxZ};
        double[] d = {step.x,step.y,step.z};
        for (int axis = 0; axis < 3; axis++) {
            if (Math.abs(d[axis]) < EPS) {
                if (hi[axis] <= blo[axis] || lo[axis] >= bhi[axis]) return 1;
            } else {
                double t1 = (blo[axis]-hi[axis])/d[axis], t2 = (bhi[axis]-lo[axis])/d[axis];
                enter = Math.max(enter, Math.min(t1,t2));
                leave = Math.min(leave, Math.max(t1,t2));
                if (leave < enter) return 1;
            }
        }
        return leave <= 0 || enter >= 1 || enter < -EPS ? 1 : Math.max(0, enter);
    }
    private static double penetration(Box a, Box b) {
        if (!a.intersects(b)) return 0;
        return Math.min(Math.min(a.maxX-b.minX,b.maxX-a.minX),
            Math.min(Math.min(a.maxY-b.minY,b.maxY-a.minY),Math.min(a.maxZ-b.minZ,b.maxZ-a.minZ)));
    }
    public static double overlapDepth(Box a, List<Box> obstacles) {
        return obstacles.stream().mapToDouble(b -> penetration(a,b)).sum();
    }
    public static Vec3d clip(Box a, Vec3d step, List<Box> obstacles) {
        double fraction = 1;
        boolean overlapping = false, improving = false;
        for (Box obstacle : obstacles) {
            double startDepth = penetration(a, obstacle);
            if (startDepth > 0) {
                overlapping = true;
                // 外から生じた重なりは、全障害物で増やさず少なくとも一つ減らす移動で脱出する。
                double depth = startDepth;
                for (int sample = 1; sample <= 4; sample++) {
                    double next = penetration(a.offset(step.multiply(sample/4.0)), obstacle);
                    if (next > depth + EPS) return Vec3d.ZERO;
                    depth = next;
                }
                if (depth < startDepth - EPS) improving = true;
            } else fraction = Math.min(fraction, contact(a, step, obstacle));
        }
        if (overlapping && !improving) return Vec3d.ZERO;
        Vec3d clipped = step.multiply(fraction < 1 ? Math.max(0, fraction-EPS) : 1);
        if (overlapping && overlapDepth(a.offset(clipped),obstacles) >= overlapDepth(a,obstacles)-EPS) return Vec3d.ZERO;
        return clipped;
    }
    public static double clearance(Box a, Box b) {
        double x = Math.max(0,Math.max(a.minX-b.maxX,b.minX-a.maxX));
        double y = Math.max(0,Math.max(a.minY-b.maxY,b.minY-a.maxY));
        double z = Math.max(0,Math.max(a.minZ-b.maxZ,b.minZ-a.maxZ));
        return Math.sqrt(x*x+y*y+z*z);
    }
    public static double clearance(Box body, List<Box> hazards) {
        return hazards.stream().mapToDouble(b -> clearance(body,b)).min().orElse(Double.POSITIVE_INFINITY);
    }
    /** 流体セルの上空を被害に数えず、実際の液面までの体積と身体の交差を返す。 */
    public static boolean touchesFluid(Box body, int x, int y, int z, double height) {
        return height > 0 && new Box(x,y,z,x+1,y+Math.min(1,height),z+1).intersects(body);
    }

    private static Vec3d local(Vec3d p, Vec3d owner, float yaw) {
        Vec3d d = p.subtract(owner), f = forward(yaw);
        return new Vec3d(-d.x*f.z+d.z*f.x, d.y, d.dotProduct(f));
    }
    private static final Box FRONT = new Box(-1.05,-.7,-.1,1.05,2.5,2.35);
    public static boolean nearFront(Vec3d p, Vec3d owner, float yaw) { return FRONT.contains(local(p,owner,yaw)); }
    public static boolean frontClear(Vec3d p, Vec3d step, Vec3d owner, float yaw) {
        Vec3d a = local(p,owner,yaw), b = local(p.add(step),owner,yaw);
        if (FRONT.contains(a)) return frontDepth(b) < frontDepth(a)-EPS;
        Box point = new Box(a.x-EPS,a.y-EPS,a.z-EPS,a.x+EPS,a.y+EPS,a.z+EPS);
        return contact(point,b.subtract(a),FRONT) >= 1;
    }
    private static double frontDepth(Vec3d p) {
        if (!FRONT.contains(p)) return 0;
        return Math.min(Math.min(p.x-FRONT.minX,FRONT.maxX-p.x),
            Math.min(Math.min(p.y-FRONT.minY,FRONT.maxY-p.y),Math.min(p.z-FRONT.minZ,FRONT.maxZ-p.z)));
    }

    /** Go around the near-front area instead of repeatedly steering against its edge. */
    public static Vec3d aroundFront(Vec3d position, Vec3d target, Vec3d owner, float yaw) {
        if (frontClear(position,target.subtract(position),owner,yaw) || nearFront(position,owner,yaw)) return target;
        Vec3d best = target;
        double score = Double.POSITIVE_INFINITY;
        for (double right : new double[]{1.4,-1.4}) for (double front : new double[]{2.8,-.55}) {
            Vec3d corner = offset(owner,yaw,front,right,position.y-owner.y);
            if (position.distanceTo(corner) < .04 || !frontClear(position,corner.subtract(position),owner,yaw)) continue;
            double cost = position.distanceTo(corner)+corner.distanceTo(target)
                + (frontClear(corner,target.subtract(corner),owner,yaw) ? 0 : 8);
            if (cost < score) { score = cost; best = corner; }
        }
        return best;
    }

    private static boolean clearSegment(Vec3d start, Vec3d target, List<Box> obstacles) {
        Box body = box(start);
        return obstacles.stream().noneMatch(b -> body.intersects(b) || contact(body,target.subtract(start),b) < 1);
    }
    private static Vec3d aroundObstacle(Vec3d position, Vec3d target, double speed, Vec3d previous,
            List<Box> obstacles, Predicate<Vec3d> stepAllowed) {
        Box body = box(position);
        if (overlapDepth(body,obstacles) > 0 || clearSegment(position,target,obstacles)) return target;
        Box obstacle = obstacles.stream().filter(b -> contact(body,target.subtract(position),b) < 1)
            .min(java.util.Comparator.comparingDouble(b -> contact(body,target.subtract(position),b))).orElse(null);
        if (obstacle == null) return target;
        double half = BodyTuning.BODY_SIZE/2, margin = .3;
        List<Vec3d> corners = new ArrayList<>();
        for (double x : new double[]{obstacle.minX-half-margin,obstacle.maxX+half+margin})
            for (double z : new double[]{obstacle.minZ-half-margin,obstacle.maxZ+half+margin}) corners.add(new Vec3d(x,position.y,z));
        corners.add(new Vec3d(position.x,obstacle.maxY+margin,position.z));
        corners.add(new Vec3d(position.x,obstacle.minY-BodyTuning.BODY_SIZE-margin,position.z));
        Vec3d best = target;
        double bestCost = Double.POSITIVE_INFINITY;
        for (Vec3d corner : corners) {
            Vec3d delta = corner.subtract(position);
            if (delta.length() < .04 || !clearSegment(position,corner,obstacles)
                    || !stepAllowed.test(delta.normalize().multiply(Math.min(speed,delta.length())))) continue;
            double cost = delta.length()+corner.distanceTo(target) + (clearSegment(corner,target,obstacles) ? 0 : 8);
            cost += Math.abs(corner.y-position.y)*.3;
            if (previous.lengthSquared() > EPS) cost -= previous.normalize().dotProduct(delta.normalize())*.4;
            if (cost < bestCost) { bestCost = cost; best = corner; }
        }
        return best;
    }

    public static Vec3d steer(Vec3d position, Vec3d target, double speed, Vec3d previous,
            List<Box> obstacles, Predicate<Vec3d> stepAllowed) {
        Vec3d routeTarget = aroundObstacle(position,target,speed,previous,obstacles,stepAllowed);
        Vec3d delta = routeTarget.subtract(position);
        Box body = box(position);
        double overlap = overlapDepth(body, obstacles);
        if (delta.lengthSquared() < 1e-8 && overlap == 0) return Vec3d.ZERO;
        Vec3d desired = delta.lengthSquared() < EPS ? new Vec3d(speed,0,0)
            : delta.normalize().multiply(Math.min(speed,delta.length()*.25));
        List<Vec3d> candidates = new ArrayList<>();
        for (double degrees : new double[]{0,45,-45,90,-90,135,-135,180}) {
            double a = Math.toRadians(degrees), c = Math.cos(a), s = Math.sin(a);
            Vec3d flat = new Vec3d(desired.x*c-desired.z*s,desired.y,desired.x*s+desired.z*c);
            for (double dy : new double[]{0,speed*.65,-speed*.65}) candidates.add(flat.add(0,dy,0));
        }
        if (overlap > 0 || desired.horizontalLengthSquared() < EPS) {
            candidates.addAll(List.of(new Vec3d(speed,0,0),new Vec3d(-speed,0,0),
                new Vec3d(0,speed,0),new Vec3d(0,-speed,0),new Vec3d(0,0,speed),new Vec3d(0,0,-speed)));
        }
        Vec3d best = Vec3d.ZERO;
        double bestScore = -Double.MAX_VALUE;
        for (Vec3d candidate : candidates) {
            Vec3d step = previous.multiply(.35).add(candidate.multiply(.65));
            if (step.length() > speed) step = step.normalize().multiply(speed);
            step = clip(body,step,obstacles);
            if (step.lengthSquared() < 1e-10 || !stepAllowed.test(step)) continue;
            double progress = delta.length()-routeTarget.distanceTo(position.add(step));
            // 目標への進捗と速度の連続性で選ぶ。移動量そのものを加点すると、開けた水平移動でも
            // 不要な上下候補が選ばれてガタつく。浮遊は安全検査後の小さな波として別に重ねる。
            double score = progress - step.subtract(previous).length()*.08;
            score += 100*(overlap-overlapDepth(body.offset(step),obstacles));
            if (score > bestScore) { bestScore = score; best = step; }
        }
        return best;
    }

    public record HoverTarget(Vec3d position, boolean applied) {}
    /** 位相を足した目標自体も検査する。低い天井等で入れない場合は基準目標へ戻し、呼出し側はそのtickの浮遊を休止する。 */
    public static HoverTarget hoverTarget(Vec3d target, double offset, Predicate<Vec3d> targetAllowed) {
        Vec3d floating = target.add(0,offset,0);
        boolean applied = targetAllowed.test(floating);
        return new HoverTarget(applied ? floating : target,applied);
    }

    /** 安全な基本移動へ小さな浮遊差分を足す。追加分が衝突・境界・危険経路へ入る場合は基本移動を保つ。 */
    public record HoverStep(Vec3d movement, boolean applied) {}
    public static HoverStep withHover(Vec3d position, Vec3d step, double vertical,
            List<Box> obstacles, Predicate<Vec3d> stepAllowed) {
        Vec3d floating = step.add(0,vertical,0);
        Vec3d clipped = clip(box(position),floating,obstacles);
        boolean applied = clipped.squaredDistanceTo(floating) < 1e-14 && stepAllowed.test(floating);
        return new HoverStep(applied ? floating : step,applied);
    }
}
