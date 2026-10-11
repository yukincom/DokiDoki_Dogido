package dogido.fabric.observation;

import net.minecraft.block.BlockState;
import net.minecraft.block.DoorBlock;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.registry.Registries;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;

public final class ObservationVisibility {
    private ObservationVisibility() {}

    private static final int OCCLUDED_AUDIO_VERTICAL_BLOCKS = 5;

    private static final int OCCLUDED_AUDIO_HORIZONTAL_BLOCKS = 8;

    public static boolean hasLineOfSight(ClientPlayerEntity player, ClientWorld world, Entity entity) {
        double height = Math.max(entity.getHeight(), 0.5);
        Vec3d[] samplePoints = new Vec3d[] {
            new Vec3d(entity.getX(), entity.getY() + height * 0.25, entity.getZ()),
            new Vec3d(entity.getX(), entity.getY() + height * 0.55, entity.getZ()),
            new Vec3d(entity.getX(), entity.getY() + height * 0.85, entity.getZ()),
        };
        for (Vec3d end : samplePoints) {
            if (hasDirectLineOfSight(player, world, end) || hasAdjacentWindowLineOfSight(player, world, end)) {
                return true;
            }
        }
        return false;
    }

    public static boolean shouldObserveOccludedAudio(ClientPlayerEntity player, ClientWorld world, Vec3d source) {
        if (hasDirectLineOfSight(player, world, source)) {
            return true;
        }
        if (hasAdjacentAcousticOpeningToward(player, world, source)) {
            return true;
        }
        BlockPos playerPos = player.getBlockPos();
        BlockPos sourcePos = BlockPos.ofFloored(source);
        return Math.abs(sourcePos.getX() - playerPos.getX()) <= OCCLUDED_AUDIO_HORIZONTAL_BLOCKS
            && Math.abs(sourcePos.getZ() - playerPos.getZ()) <= OCCLUDED_AUDIO_HORIZONTAL_BLOCKS
            && Math.abs(sourcePos.getY() - playerPos.getY()) <= OCCLUDED_AUDIO_VERTICAL_BLOCKS;
    }

    public static boolean hasDirectLineOfSight(ClientPlayerEntity player, ClientWorld world, Vec3d end) {
        HitResult hit = world.raycast(
            new RaycastContext(
                player.getEyePos(),
                end,
                RaycastContext.ShapeType.COLLIDER,
                RaycastContext.FluidHandling.NONE,
                player
            )
        );
        return hit.getType() == HitResult.Type.MISS;
    }

    private static boolean hasAdjacentWindowLineOfSight(ClientPlayerEntity player, ClientWorld world, Vec3d end) {
        Vec3d start = player.getEyePos();
        for (Direction side : Direction.Type.HORIZONTAL) {
            if (!isTargetOnSide(player, end, side)) {
                continue;
            }
            if (!hasAdjacentViewThroughBlock(world, player.getBlockPos(), side)) {
                continue;
            }
            if (hasLineThroughAdjacentViewBlocks(world, player.getBlockPos(), start, end, side)) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasAdjacentAcousticOpeningToward(ClientPlayerEntity player, ClientWorld world, Vec3d source) {
        BlockPos origin = player.getBlockPos();
        for (Direction side : Direction.Type.HORIZONTAL) {
            if (!isTargetOnSide(player, source, side)) {
                continue;
            }
            if (hasAdjacentAcousticOpening(world, origin, side)) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasLineThroughAdjacentViewBlocks(
        ClientWorld world,
        BlockPos playerBlock,
        Vec3d start,
        Vec3d end,
        Direction side
    ) {
        Vec3d delta = end.subtract(start);
        double distance = delta.length();
        if (distance <= 0.0001) {
            return true;
        }
        int steps = Math.max(8, (int) Math.ceil(distance * 8.0));
        Vec3d step = delta.multiply(1.0 / steps);
        boolean passedAdjacentViewBlock = false;

        for (int index = 1; index <= steps; index += 1) {
            Vec3d point = start.add(step.multiply(index));
            BlockPos sample = BlockPos.ofFloored(point);
            if (sample.equals(playerBlock) || sample.equals(playerBlock.up())) {
                continue;
            }
            BlockState state = world.getBlockState(sample);
            if (isVisuallyEmpty(world, sample, state)) {
                continue;
            }
            if (!passedAdjacentViewBlock) {
                if (!isImmediateSideBlock(playerBlock, sample, side) || !isViewThroughBlock(state)) {
                    return false;
                }
                passedAdjacentViewBlock = true;
                continue;
            }
            if (!isViewThroughBlock(state)) {
                return false;
            }
        }
        return passedAdjacentViewBlock;
    }

    private static boolean hasAdjacentAcousticOpening(ClientWorld world, BlockPos origin, Direction side) {
        return isAcousticOpeningBlock(world.getBlockState(origin.offset(side)))
            || isAcousticOpeningBlock(world.getBlockState(origin.up().offset(side)));
    }

    private static boolean hasAdjacentViewThroughBlock(ClientWorld world, BlockPos origin, Direction side) {
        return isViewThroughBlock(world.getBlockState(origin.offset(side)))
            || isViewThroughBlock(world.getBlockState(origin.up().offset(side)));
    }

    private static boolean isImmediateSideBlock(BlockPos origin, BlockPos sample, Direction side) {
        return sample.equals(origin.offset(side)) || sample.equals(origin.up().offset(side));
    }

    private static boolean isTargetOnSide(ClientPlayerEntity player, Vec3d target, Direction side) {
        return switch (side) {
            case EAST -> target.x > player.getX();
            case WEST -> target.x < player.getX();
            case SOUTH -> target.z > player.getZ();
            case NORTH -> target.z < player.getZ();
            default -> false;
        };
    }

    private static boolean isVisuallyEmpty(ClientWorld world, BlockPos pos, BlockState state) {
        return state.isAir() || state.getCollisionShape(world, pos).isEmpty();
    }

    private static boolean isAcousticOpeningBlock(BlockState state) {
        if (state.isAir()) {
            return false;
        }
        String path = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (state.getBlock() instanceof DoorBlock) {
            return true;
        }
        return path.endsWith("_bars")
            || path.contains("grate")
            || path.contains("lattice");
    }

    private static boolean isViewThroughBlock(BlockState state) {
        if (state.isAir()) {
            return false;
        }
        String path = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (state.getBlock() instanceof DoorBlock) {
            return true;
        }
        return path.contains("glass")
            || path.endsWith("_bars")
            || path.contains("grate")
            || path.contains("lattice");
    }

    public static boolean isWindowMaterialBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return isGlassBlock(state)
            || blockId.endsWith("_bars")
            || blockId.contains("grate")
            || blockId.contains("lattice")
            || blockId.endsWith("_fence");
    }

    public static boolean isGlassBlock(BlockState state) {
        return Registries.BLOCK.getId(state.getBlock()).getPath().contains("glass");
    }

    public static boolean isWindowSideOpen(ClientWorld world, BlockPos pos, boolean rejectFluid) {
        BlockState state = world.getBlockState(pos);
        if (rejectFluid && !state.getFluidState().isEmpty()) {
            // 水槽や水中建築のガラスを、乾いた屋内の窓と断定しない。
            return false;
        }
        return isVisuallyEmpty(world, pos, state);
    }
}
