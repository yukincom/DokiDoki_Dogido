package dogido.lab;

import java.util.ArrayList;
import java.util.Comparator;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.shape.VoxelShape;
import net.minecraft.util.shape.VoxelShapes;

/**
 * すり抜け中の眼にはスペクテイター相当の壁内視点を適用する。Minecraft raycastが順に渡す形状だけを扱い、
 * 眼から連続した固体区間を除く。一度空間へ出た先の壁は残し、移動・ダメージ・食料判定は変更しない。
 */
final class BodyVisionOrigin {
    private static final double EPSILON = 1e-9;
    private final Vec3d eye, delta;
    private double initialWallEnd;
    private record Interval(Box box, double start, double end) {}
    BodyVisionOrigin(Vec3d eye, Vec3d target, boolean eyeInsideWall) {
        this.eye = eye;
        this.delta = target.subtract(eye);
        this.initialWallEnd = eyeInsideWall ? 0 : -1;
    }

    /** 順序はMinecraftのblock raycastが所有する。別のworld走査やchunk読込みは行わない。 */
    VoxelShape outsideInitialWall(VoxelShape shape, BlockPos position) {
        if (initialWallEnd < 0) return shape;
        var intervals = new ArrayList<Interval>();
        for (Box local : shape.getBoundingBoxes()) {
            var interval = intersection(local,local.offset(position));
            if (interval != null) intervals.add(interval);
        }
        intervals.sort(Comparator.comparingDouble(Interval::start));
        VoxelShape remaining = VoxelShapes.empty();
        for (var interval : intervals) {
            if (interval.start() <= initialWallEnd+EPSILON) {
                initialWallEnd = Math.max(initialWallEnd,interval.end());
            } else {
                remaining = VoxelShapes.union(remaining,VoxelShapes.cuboid(interval.box()));
            }
        }
        return remaining;
    }

    /** 衝突形状の各直方体と線分の交差区間を[0,1]で測る。部分ブロックの隙間を全ブロック扱いで埋めない。 */
    private Interval intersection(Box local, Box box) {
        double start = 0, end = 1;
        double[] origin = {eye.x,eye.y,eye.z}, direction = {delta.x,delta.y,delta.z};
        double[] minimum = {box.minX,box.minY,box.minZ}, maximum = {box.maxX,box.maxY,box.maxZ};
        for (int axis = 0; axis < 3; axis++) {
            if (Math.abs(direction[axis]) < EPSILON) {
                if (origin[axis] < minimum[axis] || origin[axis] >= maximum[axis]) return null;
                continue;
            }
            double a = (minimum[axis]-origin[axis])/direction[axis];
            double b = (maximum[axis]-origin[axis])/direction[axis];
            start = Math.max(start,Math.min(a,b));
            end = Math.min(end,Math.max(a,b));
            if (start >= end) return null;
        }
        return new Interval(local,start,end);
    }
}
