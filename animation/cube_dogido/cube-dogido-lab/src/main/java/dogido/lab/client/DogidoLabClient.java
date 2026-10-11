package dogido.lab.client;

import dogido.lab.DogidoLab;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.rendering.v1.EntityModelLayerRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.EntityRendererRegistry;
import net.minecraft.client.render.entity.model.EntityModelLayer;

public final class DogidoLabClient implements ClientModInitializer {
    public static final EntityModelLayer LAYER = new EntityModelLayer(DogidoLab.ID, "main");

    @Override
    public void onInitializeClient() {
        EntityModelLayerRegistry.registerModelLayer(LAYER, DogidoModel::modelData);
        EntityRendererRegistry.register(DogidoLab.DOGIDO, DogidoRenderer::new);
        BodyClientBridge.initialize();
    }
}
