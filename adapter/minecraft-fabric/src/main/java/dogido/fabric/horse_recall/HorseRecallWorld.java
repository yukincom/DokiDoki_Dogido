package dogido.fabric.horse_recall;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.entity.Entity;
import net.minecraft.entity.EntityPose;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.registry.tag.FluidTags;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;

/**
 * サーバーの正本から乗れる動物と安全な着地点を読む。呼出しは必ず統合サーバースレッド上で行う。
 * 個体lookupとロード済みチャンクだけを使い、遠方のセーブ読込・強制ロードは行わない。
 */
final class HorseRecallWorld {
    private HorseRecallWorld() {}

    static boolean authorized(Entity entity, UUID player) {
        return HorseRecallChecks.authorized(HorseRecallMounts.target(entity), player);
    }

    /** 上限超過は33件目を返してSnapshotを利用不可にする。省略した部分一覧を確定一覧にしない。 */
    static List<HorseRecallProtocol.Candidate> observe(ServerWorld world, UUID player) {
        List<HorseRecallProtocol.Candidate> candidates = new ArrayList<>();
        for (Entity entity : world.iterateEntities()) {
            var target = HorseRecallMounts.target(entity);
            if (!HorseRecallChecks.authorized(target, player)) continue;
            candidates.add(new HorseRecallProtocol.Candidate(entity.getUuid(), target.kind().wireName(),
                target.customName(), target.tamed(), target.owner(), target.adult(), target.equipped(),
                target.alive(), target.dimension(), target.hasPassengers(), target.hasVehicle(), target.leashed()));
            if (candidates.size() > HorseRecallProtocol.MAX_CANDIDATES) return candidates;
        }
        candidates.sort(Comparator.comparing(candidate -> candidate.entityId().toString()));
        return List.copyOf(candidates);
    }

    static String playerError(ServerPlayerEntity player, String dimension, Entity mount) {
        if (player == null || !player.isAlive() || player.isRemoved()) return "player_unavailable";
        if (!player.getEntityWorld().getRegistryKey().getValue().toString().equals(dimension)) return "dimension_changed";
        var kind = HorseRecallMounts.kind(mount);
        // オウムガイは水中から頼める。溶岩中や、陸上種への水中依頼は既存どおり実行しない。
        boolean fluidReady = !player.isInFluid() || kind != null && kind.aquatic() && player.isTouchingWater() && !player.isInLava();
        if (player.isSpectator() || player.isSleeping() || player.hasVehicle() || !fluidReady) return "player_not_ready";
        return null;
    }

    static String horseError(Entity entity, ServerPlayerEntity player) {
        return HorseRecallChecks.targetError(HorseRecallMounts.target(entity), player.getUuid(),
            player.getEntityWorld().getRegistryKey().getValue().toString());
    }

    /**
     * 水生は横3×3の水域、陸上は平らで乾いた床、飛行は身体全体の空間を検査する。
     * standing寸法を使うので座ったラクダを狭所に出さない。全域のロード確認前にblockを読まない。
     */
    static boolean safe(ServerWorld world, MobEntity mount, HorseRecallLanding.Point point) {
        var kind = HorseRecallMounts.kind(mount);
        if (kind == null) return false;
        var dimensions = mount.getDimensions(EntityPose.STANDING);
        var volume = HorseRecallLanding.volume(point, kind, dimensions.width(), dimensions.height());
        Box region = new Box(volume.minX(), volume.minY(), volume.minZ(),
            volume.maxX() + 1, volume.maxY() + 1, volume.maxZ() + 1);
        if (!world.getWorldBorder().contains(region)) return false;
        var loaded = new HorseRecallLanding.Volume(volume.minX(),
            kind.aquatic() || kind.flying() ? volume.minY() : volume.minY() - 1,
            volume.minZ(), volume.maxX(), volume.maxY(), volume.maxZ());
        if (!loaded.all(cell -> world.getChunkManager().isChunkLoaded(cell.x() >> 4, cell.z() >> 4)
                && world.isInBuildLimit(new BlockPos(cell.x(), cell.y(), cell.z())))) return false;
        if (!volume.all(cell -> {
            BlockPos pos = new BlockPos(cell.x(), cell.y(), cell.z());
            BlockState state = world.getBlockState(pos);
            if (hazardous(state)) return false;
            if (kind.aquatic()) return state.getFluidState().isIn(FluidTags.WATER)
                && state.getCollisionShape(world, pos).isEmpty();
            return state.getFluidState().isEmpty();
        })) return false;
        if (!kind.aquatic() && !kind.flying()) {
            var floor = new HorseRecallLanding.Volume(volume.minX(), point.y() - 1, volume.minZ(),
                volume.maxX(), point.y() - 1, volume.maxZ());
            if (!floor.all(cell -> {
                BlockPos pos = new BlockPos(cell.x(), cell.y(), cell.z());
                BlockState state = world.getBlockState(pos);
                return state.isSideSolidFullSquare(world, pos, Direction.UP)
                    && state.getFluidState().isEmpty() && !hazardous(state);
            })) return false;
        }
        // 水域は9マスを確保し、身体の位置ではblockと他個体の衝突も別に検査する。
        Box space = kind.aquatic() ? dimensions.getBoxAt(point.x(), point.y(), point.z()) : region;
        return world.isSpaceEmpty(mount, space) && world.getOtherEntities(mount, space).isEmpty();
    }

    private static boolean hazardous(BlockState state) {
        return state.isOf(Blocks.FIRE) || state.isOf(Blocks.SOUL_FIRE) || state.isOf(Blocks.LAVA)
            || state.isOf(Blocks.CACTUS) || state.isOf(Blocks.MAGMA_BLOCK)
            || state.isOf(Blocks.CAMPFIRE) || state.isOf(Blocks.SOUL_CAMPFIRE)
            || state.isOf(Blocks.POWDER_SNOW) || state.isOf(Blocks.SWEET_BERRY_BUSH)
            || state.isOf(Blocks.WITHER_ROSE) || state.isOf(Blocks.NETHER_PORTAL)
            || state.isOf(Blocks.END_PORTAL) || state.isOf(Blocks.END_GATEWAY);
    }
}
