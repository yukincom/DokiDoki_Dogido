package dogido.fabric.horse_recall;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.entity.Entity;
import net.minecraft.entity.EntityType;
import net.minecraft.entity.passive.HorseEntity;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;

/**
 * サーバーの正本から所有馬と安全な足場を読む。呼出しは必ず統合サーバースレッド上で行う。
 * 個体lookupとロード済みチャンクだけを使い、遠方のセーブ読込・強制ロードは行わない。
 */
final class HorseRecallWorld {
    private HorseRecallWorld() {}

    static boolean owned(HorseEntity horse, UUID player) {
        return horse.isTame() && horse.getOwnerReference() != null
            && horse.getOwnerReference().getUuid().equals(player);
    }

    /** 上限超過は33件目を返してSnapshotを利用不可にする。省略した部分一覧を確定一覧にしない。 */
    static List<HorseRecallProtocol.Candidate> observe(ServerWorld world, UUID player) {
        List<HorseRecallProtocol.Candidate> candidates = new ArrayList<>();
        String dimension = world.getRegistryKey().getValue().toString();
        for (Entity entity : world.iterateEntities()) {
            if (!(entity instanceof HorseEntity horse) || entity.getType() != EntityType.HORSE || !owned(horse, player)) continue;
            String name = horse.getCustomName() == null ? null : horse.getCustomName().getString();
            candidates.add(new HorseRecallProtocol.Candidate(horse.getUuid(), name, horse.isTame(), true,
                horse.isAlive() && !horse.isRemoved(), dimension, horse.hasPassengers(), horse.isLeashed()));
            if (candidates.size() > HorseRecallProtocol.MAX_CANDIDATES) return candidates;
        }
        candidates.sort(Comparator.comparing(candidate -> candidate.entityId().toString()));
        return List.copyOf(candidates);
    }

    static String playerError(ServerPlayerEntity player, String dimension) {
        if (player == null || !player.isAlive() || player.isRemoved()) return "player_unavailable";
        if (!player.getEntityWorld().getRegistryKey().getValue().toString().equals(dimension)) return "dimension_changed";
        if (player.isSpectator() || player.isSleeping() || player.hasVehicle() || player.isInFluid()) return "player_not_ready";
        return null;
    }

    static String horseError(Entity entity, ServerPlayerEntity player) {
        HorseRecallChecks.Target target = entity instanceof HorseEntity horse
            ? new HorseRecallChecks.Target(entity.getType() == EntityType.HORSE, horse.isAlive() && !horse.isRemoved(),
                horse.isTame(), horse.getOwnerReference() == null ? null : horse.getOwnerReference().getUuid(),
                horse.getEntityWorld().getRegistryKey().getValue().toString(), horse.hasPassengers(), horse.hasVehicle(), horse.isLeashed())
            : null;
        return HorseRecallChecks.targetError(target, player.getUuid(), player.getEntityWorld().getRegistryKey().getValue().toString());
    }

    /**
     * 2×2の平らな床、3ブロックの空間、液体・危険物・他個体・境界を検査する。
     * チャンクの存在を全域で先に確かめてからblockを読むので、照会自体でチャンクを作らない。
     */
    static boolean safe(ServerWorld world, HorseEntity horse, HorseRecallLanding.Point point) {
        int minX = point.x() - 1;
        int minZ = point.z() - 1;
        Box space = new Box(minX, point.y(), minZ, point.x() + 1, point.y() + 3, point.z() + 1);
        if (!world.getWorldBorder().contains(space)) return false;
        for (int x = minX; x <= point.x(); x++) {
            for (int z = minZ; z <= point.z(); z++) {
                if (!world.getChunkManager().isChunkLoaded(x >> 4, z >> 4)) return false;
                if (!world.isInBuildLimit(new BlockPos(x, point.y() - 1, z))
                        || !world.isInBuildLimit(new BlockPos(x, point.y() + 2, z))) return false;
            }
        }
        for (int x = minX; x <= point.x(); x++) {
            for (int z = minZ; z <= point.z(); z++) {
                BlockPos floor = new BlockPos(x, point.y() - 1, z);
                BlockState floorState = world.getBlockState(floor);
                if (!floorState.isSideSolidFullSquare(world, floor, Direction.UP)
                        || !floorState.getFluidState().isEmpty() || hazardous(floorState)) return false;
                for (int y = point.y(); y <= point.y() + 2; y++) {
                    BlockState state = world.getBlockState(new BlockPos(x, y, z));
                    if (!state.getFluidState().isEmpty() || hazardous(state)) return false;
                }
            }
        }
        // 床だけでなく後脚で立つときの空間を確保し、プレイヤーのいる場所にも出さない。
        return world.isSpaceEmpty(horse, space) && world.getOtherEntities(horse, space).isEmpty();
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
