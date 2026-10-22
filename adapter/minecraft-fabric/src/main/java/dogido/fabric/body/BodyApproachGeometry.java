package dogido.fabric.body;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.function.Predicate;
import static dogido.fabric.body.BodyInspectProtocol.Point;
import static dogido.lab.api.BodyProtocol.followBodyHeight;
import static dogido.lab.api.BodyProtocol.inspectionHeightAllowed;

/** 対象と身体の実寸から、対象に重ならない横・斜め上下の停止位置を選ぶ。世界の衝突検査は呼出元が所有する。 */
final class BodyApproachGeometry {
    static final double CLEARANCE=.4;
    private BodyApproachGeometry() {}
    record Bounds(double minX,double minY,double minZ,double maxX,double maxY,double maxZ) {
        Bounds {
            if (!Double.isFinite(minX) || !Double.isFinite(minY) || !Double.isFinite(minZ)
                    || !Double.isFinite(maxX) || !Double.isFinite(maxY) || !Double.isFinite(maxZ)
                    || minX >= maxX || minY >= maxY || minZ >= maxZ) throw new IllegalArgumentException("bounds");
        }
        Bounds at(Point anchor) { return new Bounds(minX+anchor.x(),minY+anchor.y(),minZ+anchor.z(),maxX+anchor.x(),maxY+anchor.y(),maxZ+anchor.z()); }
        Bounds expand(double amount) { return new Bounds(minX-amount,minY-amount,minZ-amount,maxX+amount,maxY+amount,maxZ+amount); }
        boolean intersects(Bounds other) {
            return maxX > other.minX && minX < other.maxX && maxY > other.minY && minY < other.maxY && maxZ > other.minZ && minZ < other.maxZ;
        }
        double centerX() { return (minX+maxX)/2; }
        double centerY() { return (minY+maxY)/2; }
        double centerZ() { return (minZ+maxZ)/2; }
    }
    private record Candidate(Point position,int level) {}
    /** bodyShapeは身体位置に対するboxの相対座標。現在の追従半径内に停止する。clearは未ロードをfalseとして扱う。候補がなければnull。 */
    static Point choose(Point source,Point owner,double ownerEyeHeight,double followDistance,Bounds target,Bounds bodyShape,boolean ceiling,
            Predicate<Bounds> clear) {
        if (!Double.isFinite(followDistance) || followDistance <= 0 || !Double.isFinite(ownerEyeHeight)) return null;
        double followY=owner.y()+followBodyHeight(ownerEyeHeight,bodyShape.maxY()-bodyShape.minY());
        var candidates=new ArrayList<Candidate>();
        double[] ys={target.centerY()-bodyShape.centerY(),target.maxY()+CLEARANCE-bodyShape.minY(),
            target.minY()-CLEARANCE-bodyShape.maxY()};
        for (int x=-1;x<=1;x++) for (int z=-1;z<=1;z++) {
            if (x == 0 && z == 0) continue;
            double px=x < 0 ? target.minX()-CLEARANCE-bodyShape.maxX()
                : x > 0 ? target.maxX()+CLEARANCE-bodyShape.minX() : target.centerX()-bodyShape.centerX();
            double pz=z < 0 ? target.minZ()-CLEARANCE-bodyShape.maxZ()
                : z > 0 ? target.maxZ()+CLEARANCE-bodyShape.minZ() : target.centerZ()-bodyShape.centerZ();
            for (int level=0;level<ys.length;level++) {
                if (ceiling && level == 1) continue;
                try { candidates.add(new Candidate(new Point(px,ys[level],pz),level)); }
                catch (IllegalArgumentException ignored) { }
            }
        }
        Comparator<Candidate> order=Comparator.comparingDouble(candidate -> candidate.position().distance(source));
        // 天井側の空間が塞がる対象では斜め下から試し、その後に横を調べる。
        if (ceiling) order=Comparator.<Candidate>comparingInt(candidate -> candidate.level()==2 ? 0 : 1).thenComparing(order);
        candidates.sort(order);
        Bounds exclusion=target.expand(CLEARANCE/2);
        for (Candidate candidate : candidates) {
            if (candidate.position().distance(owner) > followDistance) continue;
            if (!inspectionHeightAllowed(candidate.position().y()-followY)) continue;
            Bounds destination=bodyShape.at(candidate.position());
            if (!destination.intersects(exclusion) && clear.test(destination)) return candidate.position();
        }
        return null;
    }
    static Point above(Bounds target,Bounds bodyShape) {
        return new Point(target.centerX()-bodyShape.centerX(),target.maxY()+CLEARANCE-bodyShape.minY(),target.centerZ()-bodyShape.centerZ());
    }
}
