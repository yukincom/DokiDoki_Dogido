package dogido.fabric.screenshot;

import net.minecraft.client.render.Camera;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.World;

/** 一frameだけ使う身体カメラ。元のCamera内部状態や身体の座標・向きを変更せず、描画補間済みの姿勢を固定する。 */
public final class BodyViewCamera extends Camera {
    private final LivingEntity body;
    private final float bodyDelta;
    private final Vec3d eye;
    private final float viewYaw,viewPitch;

    public BodyViewCamera(LivingEntity body,float bodyDelta) {
        this.body=body; this.bodyDelta=bodyDelta;
        eye=new Vec3d(MathHelper.lerp(bodyDelta,body.lastRenderX,body.getX()),
            MathHelper.lerp(bodyDelta,body.lastRenderY,body.getY())+body.getStandingEyeHeight(),
            MathHelper.lerp(bodyDelta,body.lastRenderZ,body.getZ()));
        viewYaw=MathHelper.lerpAngleDegrees(bodyDelta,body.lastBodyYaw,body.bodyYaw);
        viewPitch=body.getLerpedPitch(bodyDelta);
    }

    /** vanillaがplayerを渡しても撮影対象は固定。focusedEntityをbodyにし、自身のモデルを描画対象から外す。 */
    @Override public void update(World world,Entity ignored,boolean thirdPerson,boolean inverse,float ignoredDelta) {
        super.update(world,body,false,false,bodyDelta);
        setPos(eye); setRotation(viewYaw,viewPitch);
        getEnvironmentAttributeInterpolator().update(world,eye);
    }
    public float bodyDelta() { return bodyDelta; }
}
