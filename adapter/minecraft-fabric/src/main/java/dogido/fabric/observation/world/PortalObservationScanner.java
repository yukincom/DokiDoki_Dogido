package dogido.fabric.observation.world;

import dogido.fabric.observation.ObservationOrigin;

import java.util.HashMap;
import java.util.Map;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;

import static dogido.fabric.observation.ObservationVisibility.hasDirectLineOfSight;

final class PortalObservationScanner {
    private PortalObservationScanner() {}

    private static final int END_PORTAL_FRAME_SCAN_RADIUS = 5;

    private static final int GATEWAY_SCAN_RADIUS = 15;

    // ポータルブロック検知: ネザー/エンドポータルは5ブロック、エンドゲートウェイは15ブロック
    private static final int PORTAL_SCAN_RADIUS = 5;

    record PortalObservation(String type, double distance, String encounter) {}

    static PortalObservation scanNearbyPortals(ClientPlayerEntity player, ClientWorld world, BlockPos origin,
        PortalObservationTracker tracker, long tickCounter, int snapshotIntervalTicks) {
        return scanNearbyPortals(ObservationOrigin.player(player),world,origin,tracker,tickCounter,snapshotIntervalTicks);
    }
    static PortalObservation scanNearbyPortals(ObservationOrigin receiver, ClientWorld world, BlockPos origin,
        PortalObservationTracker tracker, long tickCounter, int snapshotIntervalTicks) {
        String bestType = null;
        BlockPos bestPosition = null;
        double bestDistance = Double.MAX_VALUE;
        Map<Long, String> loadedBlocks = new HashMap<>();
        Map<Long, String> nearbyPortals = new HashMap<>();
        for (int dx = -GATEWAY_SCAN_RADIUS; dx <= GATEWAY_SCAN_RADIUS; dx += 1) {
            for (int dy = -8; dy <= 8; dy += 1) {
                for (int dz = -GATEWAY_SCAN_RADIUS; dz <= GATEWAY_SCAN_RADIUS; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    // ClientWorld.isChunkLoaded always returns true; query the actual client cache.
                    if (!world.getChunkManager().isChunkLoaded(sample.getX() >> 4, sample.getZ() >> 4)) {
                        continue;
                    }
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    String portalType = switch (blockId) {
                        case "nether_portal", "end_portal", "end_gateway" -> blockId;
                        default -> "";
                    };
                    // Include portals outside their reaction radius to distinguish later approach.
                    loadedBlocks.put(sample.asLong(), portalType);
                    if (portalType.isEmpty()) continue;
                    if (receiver.body() && !receiver.visible(world,Vec3d.ofCenter(sample),sample,GATEWAY_SCAN_RADIUS)) continue;
                    double maxRange = "end_gateway".equals(portalType) ? GATEWAY_SCAN_RADIUS : PORTAL_SCAN_RADIUS;
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    if (distance <= maxRange) nearbyPortals.put(sample.asLong(), portalType);
                    if (distance <= maxRange && distance < bestDistance) {
                        bestType = portalType;
                        bestDistance = distance;
                        bestPosition = sample;
                    }
                }
            }
        }
        boolean inFrontAndVisible = false;
        if (bestPosition != null) {
            Vec3d target = Vec3d.ofCenter(bestPosition);
            inFrontAndVisible = receiver.body() ? receiver.visible(world,target,bestPosition,GATEWAY_SCAN_RADIUS)
                : receiver.player().getRotationVec(1.0F).dotProduct(target.subtract(receiver.eyePosition())) > 0
                    && hasDirectLineOfSight(receiver.player(),world,target);
        }
        String encounter = tracker.observe(
            loadedBlocks, nearbyPortals, bestPosition == null ? null : bestPosition.asLong(),
            inFrontAndVisible, tickCounter, Math.max(40L, 2L * snapshotIntervalTicks)
        );
        return bestType == null ? null : new PortalObservation(bestType, bestDistance, encounter);
    }

    static double scanNearbyEndPortalFrame(ClientWorld world, BlockPos origin) {
        double best = Double.MAX_VALUE;
        for (int dx = -END_PORTAL_FRAME_SCAN_RADIUS; dx <= END_PORTAL_FRAME_SCAN_RADIUS; dx += 1) {
            for (int dy = -END_PORTAL_FRAME_SCAN_RADIUS; dy <= END_PORTAL_FRAME_SCAN_RADIUS; dy += 1) {
                for (int dz = -END_PORTAL_FRAME_SCAN_RADIUS; dz <= END_PORTAL_FRAME_SCAN_RADIUS; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    if ("end_portal_frame".equals(blockId)) {
                        double d = Math.sqrt(sample.getSquaredDistance(origin));
                        if (d <= END_PORTAL_FRAME_SCAN_RADIUS && d < best) {
                            best = d;
                        }
                    }
                }
            }
        }
        return best == Double.MAX_VALUE ? -1 : best;
    }
}
