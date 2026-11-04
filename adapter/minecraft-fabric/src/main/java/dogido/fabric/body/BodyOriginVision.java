package dogido.fabric.body;

import dogido.fabric.observation.ObservationOrigin;
import dogido.lab.api.BodyProtocol.Visibility;
import dogido.lab.api.perception.BodyVisibility;
import dogido.lab.api.perception.BodyVisionGeometry;
import java.util.List;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import net.minecraft.block.ShapeContext;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.registry.Registries;
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
    private final BodyAttention.View attention;
    private record Ray(Vec3d target, BlockPos allowedBlock) {}
    private static final class Budget {
        int rays, missed;
        final Map<Ray,BodyVisibility.Trace> traces = new HashMap<>();
    }
    private final int rayLimit;
    private final Budget budget;
    BodyOriginVision(long tick, Vec3d eye, double yaw, double pitch) {
        this(tick,eye,yaw,pitch,null);
    }
    BodyOriginVision(long tick, Vec3d eye, double yaw, double pitch, BodyAttention.View attention) {
        this(tick,eye,yaw,pitch,attention,new Budget(),MAX_RAYS);
    }
    private BodyOriginVision(long tick, Vec3d eye, double yaw, double pitch, BodyAttention.View attention, Budget budget, int rayLimit) {
        this.tick = tick; this.eye = eye; this.yaw = yaw; this.pitch = pitch;
        this.attention = attention; this.budget = budget; this.rayLimit = rayLimit;
    }
    /** 敵を先に照合しつつ、非敵対のモブにも最大送信64個体分（各3本）の視線を予約する。 */
    BodyOriginVision forThreats() {
        return new BodyOriginVision(tick,eye,yaw,pitch,null,budget,MAX_RAYS - 64 * 3);
    }
    /** 同じ視認frameから注目候補を絞る。実rayと予算は周辺観測と共有し、同じ点を再走査しない。 */
    BodyOriginVision forAttention(BodyAttention.View view) {
        return new BodyOriginVision(tick,eye,yaw,pitch,view,budget,rayLimit);
    }
    /** 全frameの共通予算。section前後の差だけが、その走査で測れなかったrayを表す。 */
    int missedRays() { return budget.missed; }

    /** clientの実chunk cacheを見る。World.isChunkLoadedの既定trueを観測済み扱いしない。 */
    static boolean loaded(ClientWorld world, Box area) {
        if (!world.getWorldBorder().contains(area)) return false;
        for (int x = MathHelper.floor(area.minX)>>4; x <= MathHelper.floor(area.maxX)>>4; x++)
            for (int z = MathHelper.floor(area.minZ)>>4; z <= MathHelper.floor(area.maxZ)>>4; z++)
                if (!world.getChunkManager().isChunkLoaded(x,z)) return false;
        return true;
    }
    @Override public boolean entity(ClientWorld world, Entity target, double range) {
        return entity(world,target.getUuid(),target.getBoundingBox(),range);
    }
    /** 広い視野で取得済みの同一frameの個体にも、同じ実測点と集中条件を使う。 */
    boolean entity(ClientWorld world, UUID id, Box box, double range) {
        if (attention != null && !attention.entity(id)) return false;
        double x = (box.minX+box.maxX)/2, z = (box.minZ+box.maxZ)/2, h = box.getLengthY();
        return visible(world,List.of(new Vec3d(x,box.minY+h*.25,z),
            new Vec3d(x,box.minY+h*.55,z),new Vec3d(x,box.minY+h*.85,z)),null,range);
    }
    @Override public boolean point(ClientWorld world, Vec3d target, BlockPos allowedBlock, double range) {
        if (attention != null && !attention.block(allowedBlock,allowedBlock == null ? null
                : Registries.BLOCK.getId(world.getBlockState(allowedBlock).getBlock()).toString())) return false;
        return visible(world,List.of(target),allowedBlock,range);
    }
    private boolean visible(ClientWorld world, List<Vec3d> targets, BlockPos allowedBlock, double range) {
        return visible(targets,range,target -> trace(world,target,allowedBlock));
    }
    /** 周囲は通常FOV、注目候補はその中の注意範囲。集中しても視認そのものを置き換えない。 */
    boolean visible(List<Vec3d> targets, double range, Function<Vec3d,BodyVisibility.Trace> trace) {
        if (attention != null) targets = targets.stream().filter(attention::contains).toList();
        if (targets.isEmpty()) return false;
        var observation = BodyVisionGeometry.observe(tick,eye,yaw,pitch,range,
            BodyVisionGeometry.HORIZONTAL_FOV,BodyVisionGeometry.VERTICAL_FOV,targets,trace);
        return observation.playerVisibility() == Visibility.VISIBLE;
    }
    boolean takeRay() {
        if (budget.rays >= rayLimit) { budget.missed++; return false; }
        budget.rays++;
        return true;
    }
    private BodyVisibility.Trace trace(ClientWorld world, Vec3d target, BlockPos allowedBlock) {
        return trace(target,allowedBlock,() -> BodyVisibility.trace(world,area -> loaded(world,area),eye,target,
            ShapeContext.absent(),BodyVisibility.Purpose.VISION,allowedBlock));
    }
    BodyVisibility.Trace trace(Vec3d target, BlockPos allowedBlock, java.util.function.Supplier<BodyVisibility.Trace> measure) {
        var key = new Ray(target,allowedBlock);
        var retained = budget.traces.get(key);
        if (retained != null) {
            if (retained == BodyVisibility.Trace.UNAVAILABLE) budget.missed++;
            return retained;
        }
        if (!takeRay()) return BodyVisibility.Trace.UNAVAILABLE;
        var result=measure.get();
        budget.traces.put(key,result);
        if (result == BodyVisibility.Trace.UNAVAILABLE) budget.missed++;
        return result;
    }
}
