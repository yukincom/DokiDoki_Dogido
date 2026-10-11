package dogido.fabric.observation.world;

import net.minecraft.block.BlockState;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;

import static dogido.fabric.observation.MobIdentityObservation.entityTypeName;

final class BossSceneObservation {
    private BossSceneObservation() {}

    private static final int WITHER_OMEN_SCAN_RADIUS = 8;

    private static final int BOSS_OMEN_SCAN_RADIUS = 20;

    static String detectBossOmenKind(ClientPlayerEntity player, ClientWorld world, BlockPos origin) {
        if (isEnderDragonSummonScene(world, origin)) {
            return "ender_dragon_summon";
        }
        if (isEnderDragonArenaScene(world, origin)) {
            return "ender_dragon_arena";
        }
        if (isWitherAssemblyScene(world, origin)) {
            return "wither_assembly";
        }
        return null;
    }

    private static boolean isEnderDragonArenaScene(ClientWorld world, BlockPos origin) {
        String dimensionId = world.getRegistryKey().getValue().toString();
        if (!"minecraft:the_end".equals(dimensionId) && !"the_end".equals(dimensionId)) {
            return false;
        }
        int portalFrames = 0;
        int portalBlocks = 0;
        int dragonEggs = 0;
        for (int dx = -BOSS_OMEN_SCAN_RADIUS; dx <= BOSS_OMEN_SCAN_RADIUS; dx += 1) {
            for (int dy = -8; dy <= 8; dy += 1) {
                for (int dz = -BOSS_OMEN_SCAN_RADIUS; dz <= BOSS_OMEN_SCAN_RADIUS; dz += 1) {
                    String blockId = Registries.BLOCK.getId(world.getBlockState(origin.add(dx, dy, dz)).getBlock()).getPath();
                    if ("end_portal_frame".equals(blockId)) {
                        portalFrames += 1;
                    } else if ("end_portal".equals(blockId)) {
                        portalBlocks += 1;
                    } else if ("dragon_egg".equals(blockId)) {
                        dragonEggs += 1;
                    }
                }
            }
        }
        return dragonEggs > 0 || portalBlocks >= 4 || portalFrames >= 8;
    }

    private static boolean isEnderDragonSummonScene(ClientWorld world, BlockPos origin) {
        if (!isEnderDragonArenaScene(world, origin)) {
            return false;
        }
        int nearbyEndCrystals = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            new net.minecraft.util.math.Box(
                origin.getX() - 12.0, origin.getY() - 8.0, origin.getZ() - 12.0,
                origin.getX() + 13.0, origin.getY() + 9.0, origin.getZ() + 13.0
            )
        )) {
            String entityType = entityTypeName(entity);
            if ("end_crystal".equals(entityType)) {
                nearbyEndCrystals += 1;
            }
        }
        return nearbyEndCrystals >= 4;
    }

    private static boolean isWitherAssemblyScene(ClientWorld world, BlockPos origin) {
        for (int dx = -WITHER_OMEN_SCAN_RADIUS; dx <= WITHER_OMEN_SCAN_RADIUS; dx += 1) {
            for (int dy = -4; dy <= 4; dy += 1) {
                for (int dz = -WITHER_OMEN_SCAN_RADIUS; dz <= WITHER_OMEN_SCAN_RADIUS; dz += 1) {
                    BlockPos base = origin.add(dx, dy, dz);
                    if (isWitherAssemblyAt(world, base, Direction.EAST) || isWitherAssemblyAt(world, base, Direction.SOUTH)) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    private static boolean isWitherAssemblyAt(ClientWorld world, BlockPos base, Direction armDirection) {
        if (!isWitherBodyBlock(world.getBlockState(base))) {
            return false;
        }
        BlockPos leftArm = base.offset(armDirection);
        BlockPos rightArm = base.offset(armDirection.getOpposite());
        BlockPos stem = base.down();
        if (!isWitherBodyBlock(world.getBlockState(leftArm))
            || !isWitherBodyBlock(world.getBlockState(rightArm))
            || !isWitherBodyBlock(world.getBlockState(stem))) {
            return false;
        }
        return isWitherSkullBlock(world.getBlockState(base.up()))
            && isWitherSkullBlock(world.getBlockState(leftArm.up()))
            && isWitherSkullBlock(world.getBlockState(rightArm.up()));
    }

    private static boolean isWitherBodyBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return "soul_sand".equals(blockId) || "soul_soil".equals(blockId);
    }

    private static boolean isWitherSkullBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return "wither_skeleton_skull".equals(blockId) || "wither_skeleton_wall_skull".equals(blockId);
    }
}
