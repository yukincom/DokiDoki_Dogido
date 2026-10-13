package dogido.fabric.body;

import dogido.fabric.observation.ObservationOrigin;
import dogido.lab.api.BodyProtocol.Visibility;
import dogido.lab.api.perception.BodyVisibility;
import dogido.lab.api.perception.BodyVisionGeometry;
import java.util.List;
import net.minecraft.block.ShapeContext;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;

/**
 * 取得済み身体姿勢の視線を、Body API共通のFOV・ガラス・壁内rayへ接続する。player視線の規則は変更しない。
 * 一つの身体snapshotで最大4096本。未ロード・予算超過は呼出元へ知らせ、不完全な走査を「何もない」にしない。
 */
final class BodyOriginVision implements ObservationOrigin.Sight {
    static final int MAX_RAYS = 4096;
    private final long tick;
    private final Vec3d eye;
    private final double yaw, pitch;
    private int rays;
    private boolean unavailable, exhausted;
    BodyOriginVision(long tick, Vec3d eye, double yaw, double pitch) {
        this.tick = tick; this.eye = eye; this.yaw = yaw; this.pitch = pitch;
    }
    boolean unavailable() { return unavailable; }
    boolean exhausted() { return exhausted; }
    int rayCount() { return rays; }

    /** clientの実chunk cacheを見る。World.isChunkLoadedの既定trueを観測済み扱いしない。 */
    static boolean loaded(ClientWorld world, Box area) {
        if (!world.getWorldBorder().contains(area)) return false;
        for (int x = MathHelper.floor(area.minX)>>4; x <= MathHelper.floor(area.maxX)>>4; x++)
            for (int z = MathHelper.floor(area.minZ)>>4; z <= MathHelper.floor(area.maxZ)>>4; z++)
                if (!world.getChunkManager().isChunkLoaded(x,z)) return false;
        return true;
    }
    @Override public boolean entity(ClientWorld world, Entity target, double range) {
        var box = target.getBoundingBox();
        double x = (box.minX+box.maxX)/2, z = (box.minZ+box.maxZ)/2, h = box.getLengthY();
        return visible(world,List.of(new Vec3d(x,box.minY+h*.25,z),
            new Vec3d(x,box.minY+h*.55,z),new Vec3d(x,box.minY+h*.85,z)),null,range);
    }
    @Override public boolean point(ClientWorld world, Vec3d target, BlockPos allowedBlock, double range) {
        return visible(world,List.of(target),allowedBlock,range);
    }
    private boolean visible(ClientWorld world, List<Vec3d> targets, BlockPos allowedBlock, double range) {
        var observation = BodyVisionGeometry.observe(tick,eye,yaw,pitch,range,
            BodyVisionGeometry.HORIZONTAL_FOV,BodyVisionGeometry.VERTICAL_FOV,targets,target -> {
                if (rays >= MAX_RAYS) { exhausted = true; return BodyVisibility.Trace.UNAVAILABLE; }
                rays++;
                var result = BodyVisibility.trace(world,area -> loaded(world,area),eye,target,
                    ShapeContext.absent(),BodyVisibility.Purpose.VISION,allowedBlock);
                if (result == BodyVisibility.Trace.UNAVAILABLE) unavailable = true;
                return result;
            });
        return observation.playerVisibility() == Visibility.VISIBLE;
    }
}
