package dogido.fabric.observation.world;

import net.minecraft.block.BlockState;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.util.math.BlockPos;

final class WaterObservation {
    private WaterObservation() {}

    static boolean isWaterContext(ClientPlayerEntity player) {
        return player.isTouchingWater() || player.isSubmergedInWater();
    }

    static int estimateSubmergedDepthBlocks(ClientWorld world, BlockPos origin, boolean submerged) {
        if (!submerged) {
            return 0;
        }
        int depth = 0;
        BlockPos.Mutable cursor = new BlockPos.Mutable(origin.getX(), origin.getY(), origin.getZ());
        for (int step = 0; step < 8; step += 1) {
            BlockState state = world.getBlockState(cursor);
            if (state.getFluidState().isEmpty()) {
                break;
            }
            depth += 1;
            cursor.move(0, 1, 0);
        }
        return depth;
    }
}
