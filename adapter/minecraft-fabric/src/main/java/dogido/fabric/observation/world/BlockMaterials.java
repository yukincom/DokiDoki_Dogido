package dogido.fabric.observation.world;

import java.util.Set;
import net.minecraft.block.BlockState;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.registry.Registries;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;

final class BlockMaterials {
    private BlockMaterials() {}

    static String classifyBrokenBlockMaterial(BlockState state) {
        String naturalMaterial = classifyNaturalTerrainMaterial(state);
        if (!"other".equals(naturalMaterial)) {
            return naturalMaterial;
        }
        if (state.isIn(BlockTags.PICKAXE_MINEABLE)) {
            return "stone";
        }
        if (state.isIn(BlockTags.SHOVEL_MINEABLE)) {
            return "earth";
        }
        return "other";
    }

    static String classifyNaturalTerrainMaterial(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (
            blockId.endsWith("_ore")
                || state.isIn(BlockTags.COAL_ORES)
                || state.isIn(BlockTags.COPPER_ORES)
                || state.isIn(BlockTags.DIAMOND_ORES)
                || state.isIn(BlockTags.EMERALD_ORES)
                || state.isIn(BlockTags.GOLD_ORES)
                || state.isIn(BlockTags.IRON_ORES)
                || state.isIn(BlockTags.LAPIS_ORES)
                || state.isIn(BlockTags.REDSTONE_ORES)
        ) {
            return "ore";
        }
        if (
            state.isIn(BlockTags.BASE_STONE_OVERWORLD)
                || state.isIn(BlockTags.BASE_STONE_NETHER)
                || state.isIn(BlockTags.STONE_ORE_REPLACEABLES)
                || state.isIn(BlockTags.DEEPSLATE_ORE_REPLACEABLES)
                || Set.of(
                    "tuff",
                    "calcite",
                    "dripstone_block",
                    "pointed_dripstone",
                    "blackstone",
                    "basalt",
                    "smooth_basalt",
                    "end_stone"
                ).contains(blockId)
        ) {
            return "stone";
        }
        if (
            state.isIn(BlockTags.DIRT)
                || state.isIn(BlockTags.SAND)
                || Set.of("gravel", "clay", "mud", "packed_mud").contains(blockId)
        ) {
            return "earth";
        }
        return "other";
    }

    static boolean isAirExposed(ClientWorld world, BlockPos pos) {
        for (Direction direction : Direction.values()) {
            if (world.getBlockState(pos.offset(direction)).isAir()) {
                return true;
            }
        }
        return false;
    }

}
