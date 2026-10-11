package dogido.fabric.observation.world;

import net.minecraft.block.BlockState;
import net.minecraft.state.property.Properties;

public final class CampfireObservation {
    private CampfireObservation() {}

    public static boolean isLitCampfire(BlockState state, String blockId) {
        if (!"campfire".equals(blockId) && !"soul_campfire".equals(blockId)) {
            return false;
        }
        return !state.contains(Properties.LIT) || Boolean.TRUE.equals(state.get(Properties.LIT));
    }
}
