package dogido.fabric.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import dogido.fabric.screenshot.BodyViewCapture;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.render.Camera;
import net.minecraft.client.render.WorldRenderer;
import net.minecraft.client.render.entity.state.EntityRenderState;
import net.minecraft.entity.Entity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;

/** 身体カメラからlocal playerを描けるよう狭い除外条件だけを補い、その実描画stateを診断へ写す。 */
@Mixin(WorldRenderer.class)
public abstract class BodyViewWorldRendererMixin {
    /** 1.21.11の第4呼出しはlocal playerだけの除外検査。先のbody自身の一人称除外は変更しない。 */
    @WrapOperation(method="fillEntityRenderStates",at=@At(value="INVOKE",target="Lnet/minecraft/client/render/Camera;getFocusedEntity()Lnet/minecraft/entity/Entity;",ordinal=3))
    private Entity dogido$allowOwner(Camera camera,Operation<Entity> original) {
        Entity actual=original.call(camera);
        return BodyViewCapture.activeFor(camera)?MinecraftClient.getInstance().player:actual;
    }
    @WrapOperation(method="fillEntityRenderStates",at=@At(value="INVOKE",target="Lnet/minecraft/client/render/WorldRenderer;getAndUpdateRenderState(Lnet/minecraft/entity/Entity;F)Lnet/minecraft/client/render/entity/state/EntityRenderState;"))
    private EntityRenderState dogido$ownerFrame(WorldRenderer renderer,Entity entity,float delta,Operation<EntityRenderState> original) {
        EntityRenderState state=original.call(renderer,entity,delta);
        BodyViewCapture.owner(entity,state,delta);
        return state;
    }
}
