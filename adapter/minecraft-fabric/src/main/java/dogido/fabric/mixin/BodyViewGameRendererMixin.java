package dogido.fabric.mixin;

import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import com.mojang.blaze3d.buffers.GpuBufferSlice;
import dogido.fabric.screenshot.BodyViewCapture;
import net.minecraft.client.render.Camera;
import net.minecraft.client.render.GameRenderer;
import net.minecraft.client.render.RenderTickCounter;
import net.minecraft.client.render.WorldRenderer;
import net.minecraft.client.util.ObjectAllocator;
import net.minecraft.client.util.math.MatrixStack;
import org.joml.Matrix4f;
import org.joml.Vector4f;
import org.spongepowered.asm.mixin.*;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.*;

/** 明示撮影frameだけCamera参照を交換し、描画完了・例外のどちらでも元の参照へ復帰する。 */
@Mixin(GameRenderer.class)
public abstract class BodyViewGameRendererMixin {
    @Shadow @Final @Mutable private Camera camera;

    @WrapMethod(method="render")
    private void dogido$render(RenderTickCounter counter,boolean tick,Operation<Void> original) {
        Camera saved=camera;
        var frame=BodyViewCapture.begin(counter,tick);
        try {
            if(frame!=null)camera=frame.camera();
            original.call(counter,tick);
        } finally {
            camera=saved;
            BodyViewCapture.end(frame,camera==saved);
        }
    }

    @WrapOperation(method="renderWorld",at=@At(value="INVOKE",target="Lnet/minecraft/client/render/WorldRenderer;render(Lnet/minecraft/client/util/ObjectAllocator;Lnet/minecraft/client/render/RenderTickCounter;ZLnet/minecraft/client/render/Camera;Lorg/joml/Matrix4f;Lorg/joml/Matrix4f;Lorg/joml/Matrix4f;Lcom/mojang/blaze3d/buffers/GpuBufferSlice;Lorg/joml/Vector4f;Z)V"))
    private void dogido$captureWorld(WorldRenderer renderer,ObjectAllocator allocator,RenderTickCounter counter,
            boolean outline,Camera view,Matrix4f position,Matrix4f projection,Matrix4f frustum,
            GpuBufferSlice fog,Vector4f color,boolean sky,Operation<Void> original) {
        BodyViewCapture.matrices(view,position,projection);
        original.call(renderer,allocator,counter,BodyViewCapture.activeFor(view)?false:outline,view,position,projection,frustum,fog,color,sky);
        BodyViewCapture.capture(view);
    }
    /** playerの歩行・被弾の揺れを身体カメラへ転写しない。通常frameはvanillaのまま。 */
    @Inject(method="bobView",at=@At("HEAD"),cancellable=true)
    private void dogido$noPlayerBob(MatrixStack matrices,float delta,CallbackInfo ci) { if(BodyViewCapture.active())ci.cancel(); }
    @Inject(method="tiltViewWhenHurt",at=@At("HEAD"),cancellable=true)
    private void dogido$noPlayerHurtTilt(MatrixStack matrices,float delta,CallbackInfo ci) { if(BodyViewCapture.active())ci.cancel(); }
    /** 試験frameがワールド選択画面のアイコンへ保存される副作用を避ける。 */
    @Inject(method="updateWorldIcon",at=@At("HEAD"),cancellable=true)
    private void dogido$skipWorldIcon(CallbackInfo ci) { if(BodyViewCapture.active())ci.cancel(); }
}
