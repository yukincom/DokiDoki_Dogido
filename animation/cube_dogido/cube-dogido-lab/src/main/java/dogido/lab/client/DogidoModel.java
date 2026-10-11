package dogido.lab.client;

import dogido.lab.IdleMotion;
import net.minecraft.client.model.*;
import net.minecraft.client.render.entity.model.EntityModel;
import net.minecraft.client.render.entity.state.LivingEntityRenderState;

/** Geometry and UV coordinates from the user's cube_dogido.java export. */
public final class DogidoModel extends EntityModel<LivingEntityRenderState> {
    private final ModelPart left;
    private final ModelPart right;

    public DogidoModel(ModelPart root) {
        super(root);
        ModelPart bone = root.getChild("bone");
        left = bone.getChild("left");
        right = bone.getChild("right");
    }

    public static TexturedModelData modelData() {
        ModelData data = new ModelData();
        ModelPartData bone = data.getRoot().addChild("bone",
            ModelPartBuilder.create().uv(0, 0).cuboid(-5, -6, -5, 10, 10, 10),
            ModelTransform.origin(0, 20, 0));
        bone.addChild("left",
            ModelPartBuilder.create().uv(16, 20).cuboid(0, -1, -2, 3, 1, 4),
            ModelTransform.origin(5, 0, 0));
        bone.addChild("right",
            ModelPartBuilder.create().uv(0, 20).cuboid(-3, -1, -2, 3, 1, 4),
            ModelTransform.origin(-5, 0, 0));
        return TexturedModelData.of(data, 64, 64);
    }

    @Override
    public void setAngles(LivingEntityRenderState state) {
        super.setAngles(state);
        float seconds = state.age / 20F;
        left.pitch = IdleMotion.angle(true, 0, seconds);
        left.yaw = IdleMotion.angle(true, 1, seconds);
        left.roll = IdleMotion.angle(true, 2, seconds);
        right.pitch = IdleMotion.angle(false, 0, seconds);
        right.yaw = IdleMotion.angle(false, 1, seconds);
        right.roll = IdleMotion.angle(false, 2, seconds);
    }
}
