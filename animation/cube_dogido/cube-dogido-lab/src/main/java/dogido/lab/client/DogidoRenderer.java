package dogido.lab.client;

import dogido.lab.DogidoEntity;
import net.minecraft.client.render.entity.EntityRendererFactory;
import net.minecraft.client.render.entity.MobEntityRenderer;
import net.minecraft.client.render.entity.state.LivingEntityRenderState;
import net.minecraft.util.Identifier;

public final class DogidoRenderer extends MobEntityRenderer<DogidoEntity, LivingEntityRenderState, DogidoModel> {
    private static final Identifier TEXTURE = Identifier.of("dogido_experiment", "textures/entity/dogido.png");

    public DogidoRenderer(EntityRendererFactory.Context context) {
        super(context, new DogidoModel(context.getPart(DogidoLabClient.LAYER)), .3F);
    }

    @Override
    public LivingEntityRenderState createRenderState() {
        return new LivingEntityRenderState();
    }

    @Override
    public Identifier getTexture(LivingEntityRenderState state) {
        return TEXTURE;
    }
}
