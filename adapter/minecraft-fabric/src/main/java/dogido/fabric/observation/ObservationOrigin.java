package dogido.fabric.observation;

import java.util.UUID;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.registry.tag.FluidTags;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/**
 * 観測の受け手を明示する不変値。位置・眼・向きと視認手段を渡し、collectorの状態やplayerの所有権を変更しない。
 * player用wrapperは従来の視認条件を保持する。身体用は検証済snapshotから作り、欠損をplayerで補完しない。
 */
public record ObservationOrigin(UUID id, UUID threatTargetId, Vec3d position, Vec3d eyePosition, float yaw, float pitch,
        Box bounds, ClientPlayerEntity player, Sight sight) {
    public interface Sight {
        boolean entity(ClientWorld world, Entity target, double range);
        boolean point(ClientWorld world, Vec3d target, BlockPos allowedBlock, double range);
    }

    public static ObservationOrigin player(ClientPlayerEntity player) {
        return new ObservationOrigin(player.getUuid(), player.getUuid(), player.getEntityPos(), player.getEyePos(),
            player.getYaw(), player.getPitch(), player.getBoundingBox(), player, null);
    }
    public boolean body() { return player == null; }
    public BlockPos blockPosition() { return BlockPos.ofFloored(position); }
    public double distanceTo(Vec3d target) { return position.distanceTo(target); }
    public double distanceTo(Entity target) { return distanceTo(target.getEntityPos()); }
    public boolean isSelf(Entity target) { return id.equals(target.getUuid()); }
    public boolean visible(ClientWorld world, Entity target, double range) {
        return body() ? sight.entity(world,target,range) : ObservationVisibility.hasLineOfSight(player,world,target);
    }
    public boolean visible(ClientWorld world, Vec3d target, BlockPos allowedBlock, double range) {
        return body() ? sight.point(world,target,allowedBlock,range)
            : ObservationVisibility.hasDirectLineOfSight(player,world,target);
    }
    /** 身体の眼が水面より下かを同じ観測地点で測る。playerの水中フラグを借用しない。 */
    public boolean submerged(ClientWorld world) {
        if (!body()) return player.isSubmergedInWater();
        BlockPos pos = BlockPos.ofFloored(eyePosition);
        var fluid = world.getFluidState(pos);
        return fluid.isIn(FluidTags.WATER) && eyePosition.y < pos.getY()+fluid.getHeight(world,pos);
    }
    public boolean touchingWater(ClientWorld world) {
        if (!body()) return player.isTouchingWater();
        // 身体の実寸をserver姿勢へ移した箱と水面を照合する。水のあるblockに足元が入るだけでは接水にしない。
        return sampleWater(bounds, pos -> {
            var fluid = world.getFluidState(pos);
            return fluid.isIn(FluidTags.WATER) ? pos.getY() + fluid.getHeight(world, pos) : Double.NEGATIVE_INFINITY;
        });
    }
    /** Nativeと同じ縮小箱・上端を含まないblock範囲で調べる。probeは水面Y、非水は負の無限大を返す。 */
    static boolean sampleWater(Box bounds, java.util.function.ToDoubleFunction<BlockPos> waterSurface) {
        Box contact = bounds.contract(0.001);
        BlockPos last = new BlockPos((int)Math.ceil(contact.maxX)-1, (int)Math.ceil(contact.maxY)-1,
            (int)Math.ceil(contact.maxZ)-1);
        for (BlockPos pos : BlockPos.iterate(BlockPos.ofFloored(contact.minX, contact.minY, contact.minZ), last)) {
            if (waterSurface.applyAsDouble(pos) >= contact.minY) return true;
        }
        return false;
    }
    /** 身体の同じ位置・眼・実寸で接触を採る。client補間位置やplayerの接地・炎上を借用しない。 */
    public com.google.gson.JsonObject environment(ClientWorld world) {
        if (!body()) return MobEnvironmentObservation.capture(player).toJson();
        boolean water = touchingWater(world);
        boolean rain = MobEnvironmentObservation.touchingRain(position, bounds, world);
        var value = new com.google.gson.JsonObject();
        value.addProperty("touching_water", water);
        value.addProperty("submerged_in_water", submerged(world));
        value.addProperty("touching_rain", rain);
        value.addProperty("touching_water_or_rain", water || rain);
        return value;
    }
}
