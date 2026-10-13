package dogido.fabric.observation.world;

import dogido.fabric.observation.world.ShelterObservation.LocalSpaceObservation;
import net.minecraft.block.BlockState;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;

import static dogido.fabric.observation.ObservationNumbers.clamp01;
import static dogido.fabric.observation.world.CampfireObservation.isLitCampfire;
import static dogido.fabric.observation.world.ShelterObservation.isOpenMedium;

final class LightObservation {
    private LightObservation() {}

    private static final double LIT_INTERIOR_SAFE_MAX_CEILING_HEIGHT = 5.0;

    private static final double LIT_INTERIOR_SAFE_MIN_SPAWN_DISTANCE = 4.0;

    private static final int LIT_INTERIOR_SAFE_MAX_CONNECTED_VOLUME = 24;

    private static final int LIT_INTERIOR_SAFE_LIGHT_THRESHOLD = 9;

    static int estimateConnectedDarkVolume(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -6; dx <= 6; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -6; dz <= 6; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (isOpenMedium(world.getBlockState(sample)) && world.getLightLevel(sample) <= 7) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    static double estimateNearestDarkSpawnDistance(ClientWorld world, BlockPos origin) {
        double nearest = 999.0;
        for (int dx = -8; dx <= 8; dx += 1) {
            for (int dy = -4; dy <= 4; dy += 1) {
                for (int dz = -8; dz <= 8; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (!isOpenMedium(world.getBlockState(sample))) {
                        continue;
                    }
                    if (world.getLightLevel(sample) > 7) {
                        continue;
                    }
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    if (distance < nearest) {
                        nearest = distance;
                    }
                }
            }
        }
        return nearest == 999.0 ? 99.0 : nearest;
    }

    static double estimateDangerDarkness(
        LocalSpaceObservation space,
        double enclosureScore,
        int connectedDarkVolume,
        double nearestDarkSpawnDistance,
        int nearbyLightSourceCount,
        double nearestLightSourceDistance,
        int cardinalWallCount
    ) {
        int localLight = space.localLight();
        double ceilingHeight = space.overhead().clearance();
        double lightPenalty = (15.0 - localLight) / 15.0;
        double darkVolumeScore = Math.min(1.0, connectedDarkVolume / 80.0);
        double spawnDistanceScore = nearestDarkSpawnDistance >= 99.0
            ? 0.0
            : Math.max(0.0, (12.0 - nearestDarkSpawnDistance) / 12.0);
        double nearbyLightSourceRelief = nearbyLightSourceCount <= 0 || nearestLightSourceDistance >= 99.0
            ? 0.0
            : Math.max(0.0, (4.0 - nearestLightSourceDistance) / 4.0);
        double skyPenalty = space.skyVisible() ? 0.0 : 0.15;
        boolean litInteriorPocket = isLitInteriorSafePocket(
            space,
            connectedDarkVolume,
            nearestDarkSpawnDistance,
            nearbyLightSourceCount,
            nearestLightSourceDistance
        );
        if (litInteriorPocket) {
            return clamp01(
                lightPenalty * 0.18
                    + darkVolumeScore * 0.12
                    + spawnDistanceScore * 0.08
                    - nearbyLightSourceRelief * 0.25
            );
        }
        boolean crampedDarkBurrow = !space.skyVisible()
            && ceilingHeight <= 3.0
            && cardinalWallCount >= 3
            && connectedDarkVolume <= 12;
        if (crampedDarkBurrow) {
            return clamp01(
                lightPenalty * 0.15
                    + darkVolumeScore * 0.25
                    + spawnDistanceScore * 0.1
                    - nearbyLightSourceRelief * 0.2
            );
        }
        return clamp01(
            lightPenalty * 0.35
                + enclosureScore * 0.2
                + darkVolumeScore * 0.3
                + spawnDistanceScore * 0.15
                + skyPenalty
                - nearbyLightSourceRelief * 0.3
        );
    }

    static boolean isLitInteriorSafePocket(
        LocalSpaceObservation space,
        int connectedDarkVolume,
        double nearestDarkSpawnDistance,
        int nearbyLightSourceCount,
        double nearestLightSourceDistance
    ) {
        int localLight = space.localLight();
        double ceilingHeight = space.overhead().clearance();
        String overheadCoverType = space.overhead().coverType();
        if (space.skyVisible()) {
            return false;
        }
        boolean nearbyLightSource = nearbyLightSourceCount > 0
            && nearestLightSourceDistance <= LIT_INTERIOR_SAFE_MIN_SPAWN_DISTANCE;
        if (localLight < LIT_INTERIOR_SAFE_LIGHT_THRESHOLD && !nearbyLightSource) {
            return false;
        }
        if ("foliage".equals(overheadCoverType) || "fluid".equals(overheadCoverType)) {
            return false;
        }
        if (ceilingHeight > LIT_INTERIOR_SAFE_MAX_CEILING_HEIGHT) {
            return false;
        }
        if (connectedDarkVolume > LIT_INTERIOR_SAFE_MAX_CONNECTED_VOLUME) {
            return false;
        }
        return nearestDarkSpawnDistance >= LIT_INTERIOR_SAFE_MIN_SPAWN_DISTANCE;
    }

    static int countNearbyLightSources(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -4; dx <= 4; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -4; dz <= 4; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (isNearbyLightSource(world.getBlockState(sample))) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    static double estimateNearestLightSourceDistance(ClientWorld world, BlockPos origin) {
        double nearest = 999.0;
        for (int dx = -4; dx <= 4; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -4; dz <= 4; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (!isNearbyLightSource(world.getBlockState(sample))) {
                        continue;
                    }
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    if (distance < nearest) {
                        nearest = distance;
                    }
                }
            }
        }
        return nearest == 999.0 ? 99.0 : nearest;
    }

    private static boolean isNearbyLightSource(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if ("fire".equals(blockId) || "soul_fire".equals(blockId)) {
            return true;
        }
        if (isLitCampfire(state, blockId)) {
            return true;
        }
        return state.getLuminance() >= 10;
    }

    static int countNearbyDamagingLightSources(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -5; dx <= 5; dx += 1) {
            for (int dy = -1; dy <= 2; dy += 1) {
                for (int dz = -5; dz <= 5; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (isDamagingLightSource(world.getBlockState(sample))) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    static double estimateNearestDamagingLightSourceDistance(ClientWorld world, BlockPos origin) {
        double nearest = 999.0;
        for (int dx = -5; dx <= 5; dx += 1) {
            for (int dy = -1; dy <= 2; dy += 1) {
                for (int dz = -5; dz <= 5; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (!isDamagingLightSource(world.getBlockState(sample))) {
                        continue;
                    }
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    if (distance < nearest) {
                        nearest = distance;
                    }
                }
            }
        }
        return nearest == 999.0 ? 99.0 : nearest;
    }

    private static boolean isDamagingLightSource(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if ("fire".equals(blockId) || "soul_fire".equals(blockId)) {
            return true;
        }
        if (isLitCampfire(state, blockId)) {
            return true;
        }
        return "magma_block".equals(blockId)
            || "lava".equals(blockId)
            || "lava_cauldron".equals(blockId);
    }

    static boolean isStandingOnMagmaBlock(ClientWorld world, ClientPlayerEntity player) {
        return isStandingOnMagmaBlock(world,player.getBlockPos());
    }
    static boolean isStandingOnMagmaBlock(ClientWorld world, BlockPos origin) {
        BlockPos blockBelow = origin.down();
        String blockId = Registries.BLOCK.getId(world.getBlockState(blockBelow).getBlock()).getPath();
        return "magma_block".equals(blockId);
    }
}
