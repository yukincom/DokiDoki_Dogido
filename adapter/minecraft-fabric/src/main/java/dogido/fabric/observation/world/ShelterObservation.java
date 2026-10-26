package dogido.fabric.observation.world;

import dogido.fabric.observation.ObservationOrigin;

import java.util.List;
import java.util.function.Function;
import net.minecraft.block.BlockState;
import net.minecraft.block.enums.DoubleBlockHalf;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.passive.VillagerEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.registry.Registries;
import net.minecraft.state.property.Properties;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;

import static dogido.fabric.observation.ObservationNumbers.clamp01;
import static dogido.fabric.observation.ObservationVisibility.isGlassBlock;
import static dogido.fabric.observation.ObservationVisibility.isWindowMaterialBlock;
import static dogido.fabric.observation.ObservationVisibility.isWindowSideOpen;
import static dogido.fabric.observation.world.BlockMaterials.classifyNaturalTerrainMaterial;

/** 遮蔽・建具・局所空間を観測する。集計は読取りだけで、警告や状態変更は呼出し元が所有する。 */
public final class ShelterObservation {
    private ShelterObservation() {}

    record LocalSpaceObservation(int localLight, boolean skyVisible, OverheadObservation overhead) {}

    static LocalSpaceObservation observeLocalSpace(ClientWorld world, BlockPos origin) {
        int localLight = world.getLightLevel(origin);
        boolean skyVisible = world.isSkyVisible(origin);
        OverheadObservation overhead = OverheadObservation.scan(step -> {
            BlockState state = world.getBlockState(origin.up(step));
            if (!state.getFluidState().isEmpty()) return "fluid";
            return state.isAir() ? "air" : classifyCoverBlock(state);
        });
        return new LocalSpaceObservation(localLight, skyVisible, overhead);
    }

    private static String classifyCoverBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (blockId.contains("leaves") || blockId.contains("vine") || blockId.contains("moss")) {
            return "foliage";
        }
        if (blockId.endsWith("_log") || blockId.endsWith("_wood") || blockId.contains("stem") || blockId.contains("hyphae")) {
            return "wood";
        }
        String naturalMaterial = classifyNaturalTerrainMaterial(state);
        if (!"other".equals(naturalMaterial)) {
            return naturalMaterial;
        }
        return "solid";
    }

    static double estimateEnclosureScore(ClientWorld world, BlockPos origin, boolean skyVisible) {
        int solid = 0;
        int total = 0;
        for (int dx = -2; dx <= 2; dx += 1) {
            for (int dy = -1; dy <= 1; dy += 1) {
                for (int dz = -2; dz <= 2; dz += 1) {
                    if (dx == 0 && dy == 0 && dz == 0) {
                        continue;
                    }
                    BlockPos sample = origin.add(dx, dy, dz);
                    BlockState state = world.getBlockState(sample);
                    total += 1;
                    if (!isOpenMedium(state)) {
                        solid += 1;
                    }
                }
            }
        }
        double base = total == 0 ? 0.0 : (double) solid / (double) total;
        if (!skyVisible) {
            base += 0.15;
        }
        return clamp01(base);
    }

    static boolean isOpenMedium(BlockState state) {
        return state.isAir() || !state.getFluidState().isEmpty();
    }

    enum DoorKind { NONE, CLOSED, OPEN }
    record Doors(int count, int openCount) {}

    /** 上半分を除く同じドアを一回だけ読み、総数と開いている数を同時に返す。 */
    static Doors observeNearbyDoors(ClientWorld world, BlockPos origin) {
        return observeNearbyDoors(origin, sample -> {
            BlockState state = world.getBlockState(sample);
            String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
            if (!blockId.endsWith("_door") || state.contains(Properties.DOUBLE_BLOCK_HALF)
                    && state.get(Properties.DOUBLE_BLOCK_HALF) != DoubleBlockHalf.LOWER) return DoorKind.NONE;
            return state.contains(Properties.OPEN) && Boolean.TRUE.equals(state.get(Properties.OPEN))
                ? DoorKind.OPEN : DoorKind.CLOSED;
        });
    }

    static Doors observeNearbyDoors(BlockPos origin, Function<BlockPos, DoorKind> door) {
        int count = 0, openCount = 0;
        for (int dx = -2; dx <= 2; dx++) {
            for (int dy = -1; dy <= 2; dy++) {
                for (int dz = -2; dz <= 2; dz++) {
                    DoorKind kind = door.apply(origin.add(dx, dy, dz));
                    if (kind == DoorKind.NONE) continue;
                    count++;
                    if (kind == DoorKind.OPEN) openCount++;
                }
            }
        }
        return new Doors(count, openCount);
    }

    static boolean hasNearbyWindow(ClientWorld world, BlockPos origin) {
        for (int dx = -8; dx <= 8; dx += 1) {
            for (int dy = -2; dy <= 4; dy += 1) {
                for (int dz = -8; dz <= 8; dz += 1) {
                    if (dx * dx + dz * dz > 64) {
                        continue;
                    }
                    BlockPos sample = origin.add(dx, dy, dz);
                    BlockState state = world.getBlockState(sample);
                    if (!isWindowMaterialBlock(state)) {
                        continue;
                    }
                    boolean glass = isGlassBlock(state);
                    boolean eastWestView = isWindowSideOpen(world, sample.east(), glass)
                        && isWindowSideOpen(world, sample.west(), glass);
                    boolean northSouthView = isWindowSideOpen(world, sample.north(), glass)
                        && isWindowSideOpen(world, sample.south(), glass);
                    if (eastWestView || northSouthView) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    public static int countNearbyBeds(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -5; dx <= 5; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -5; dz <= 5; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (sample.getSquaredDistance(origin) > 25.0) {
                        continue;
                    }
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    if (blockId.endsWith("_bed")) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    static int countNearbySleepingPeople(ClientWorld world, ClientPlayerEntity player) {
        return countNearbySleepingPeople(world,ObservationOrigin.player(player));
    }
    static int countNearbySleepingPeople(ClientWorld world, ObservationOrigin origin) {
        List<Entity> entities = world.getOtherEntities(
            origin.player(),
            origin.bounds().expand(5.0, 3.0, 5.0),
            entity -> entity.isAlive()
                && entity instanceof LivingEntity living
                && living.isSleeping()
                && (entity instanceof VillagerEntity || entity instanceof PlayerEntity)
        );

        int count = 0;
        for (Entity entity : entities) {
            if (!origin.isSelf(entity) && origin.distanceTo(entity) <= 5.0) {
                count += 1;
            }
        }
        return count;
    }

    static int countCardinalWalls(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (Direction direction : new Direction[] {
            Direction.NORTH,
            Direction.SOUTH,
            Direction.EAST,
            Direction.WEST,
        }) {
            if (isCardinalWall(world, origin, direction)) {
                count += 1;
            }
        }
        return count;
    }

    private static boolean isCardinalWall(ClientWorld world, BlockPos origin, Direction direction) {
        BlockPos foot = origin.offset(direction);
        BlockPos head = foot.up();
        return isShelterWallBlock(world.getBlockState(foot)) && isShelterWallBlock(world.getBlockState(head));
    }

    static int countDoubleHeightOpenSides(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (Direction direction : new Direction[] {
            Direction.NORTH,
            Direction.SOUTH,
            Direction.EAST,
            Direction.WEST,
        }) {
            if (isDoubleHeightOpenSide(world, origin, direction)) {
                count += 1;
            }
        }
        return count;
    }

    private static boolean isDoubleHeightOpenSide(ClientWorld world, BlockPos origin, Direction direction) {
        BlockPos foot = origin.offset(direction);
        BlockPos head = foot.up();
        return isOpenMedium(world.getBlockState(foot)) && isOpenMedium(world.getBlockState(head));
    }

    private static boolean isShelterWallBlock(BlockState state) {
        if (isOpenMedium(state)) {
            return false;
        }
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return !blockId.endsWith("_door")
            && !blockId.endsWith("_trapdoor")
            && !blockId.endsWith("_fence_gate");
    }

    static int countDraftyOpenings(ClientWorld world, BlockPos origin) {
        int count = 0;
        int[] perimeter = {-3, -2, -1, 0, 1, 2, 3};
        for (int dy = 0; dy <= 1; dy += 1) {
            for (int edge : perimeter) {
                count += isDraftyOpening(world, origin, edge, dy, -3, 0, 0, -1) ? 1 : 0;
                count += isDraftyOpening(world, origin, edge, dy, 3, 0, 0, 1) ? 1 : 0;
                count += isDraftyOpening(world, origin, -3, dy, edge, -1, 0, 0) ? 1 : 0;
                count += isDraftyOpening(world, origin, 3, dy, edge, 1, 0, 0) ? 1 : 0;
            }
        }
        return count;
    }

    private static boolean isDraftyOpening(
        ClientWorld world,
        BlockPos origin,
        int dx,
        int dy,
        int dz,
        int outwardX,
        int outwardY,
        int outwardZ
    ) {
        BlockPos sample = origin.add(dx, dy, dz);
        BlockState state = world.getBlockState(sample);
        if (!isOpenMedium(state)) {
            return false;
        }
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (blockId.contains("glass") || blockId.contains("bars") || blockId.contains("fence")) {
            return false;
        }
        BlockPos outward = sample.add(outwardX, outwardY, outwardZ);
        BlockState outwardState = world.getBlockState(outward);
        return isOpenMedium(outwardState);
    }

    static boolean isSafeZoneWithDoor(
        LocalSpaceObservation space,
        boolean submerged,
        int nearbyDoorCount,
        double enclosureScore
    ) {
        if (submerged || nearbyDoorCount <= 0) {
            return false;
        }
        return space.localLight() >= 8 && (
            enclosureScore >= 0.18
                || space.overhead().clearance() <= 5.0
                || !space.skyVisible()
        );
    }
}
