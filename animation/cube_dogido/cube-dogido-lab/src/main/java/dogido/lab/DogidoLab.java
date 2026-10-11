package dogido.lab;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.object.builder.v1.entity.FabricDefaultAttributeRegistry;
import net.minecraft.entity.EntityType;
import net.minecraft.entity.SpawnGroup;
import net.minecraft.registry.Registries;
import net.minecraft.registry.Registry;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.util.Identifier;

public final class DogidoLab implements ModInitializer {
    public static final Identifier ID = Identifier.of("dogido_experiment", "dogido");
    public static final RegistryKey<EntityType<?>> KEY = RegistryKey.of(RegistryKeys.ENTITY_TYPE, ID);
    public static final EntityType<DogidoEntity> DOGIDO = Registry.register(
        Registries.ENTITY_TYPE, KEY,
        EntityType.Builder.create(DogidoEntity::new, SpawnGroup.CREATURE)
            .dimensions(0.625F, 0.625F).eyeHeight(0.4F)
            .maxTrackingRange(10).trackingTickInterval(1).build(KEY));

    @Override
    public void onInitialize() {
        FabricDefaultAttributeRegistry.register(DOGIDO, DogidoEntity.attributes());
        BodyManager.initialize();
        BodyNetworking.initialize();
        BodyCommands.initialize();
    }
}
