package dogido.fabric.observation.player;

import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;

public final class ItemIdentity {
    private ItemIdentity() {}

    public static String itemId(ItemStack stack) {
        return stack.isEmpty() ? "minecraft:air" : Registries.ITEM.getId(stack.getItem()).toString();
    }
}
