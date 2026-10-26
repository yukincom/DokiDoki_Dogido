package dogido.lab;

import dogido.lab.api.BodyProtocol.Position;
import dogido.lab.api.perception.BodyVisionGeometry;
import dogido.lab.control.DirectedLook;
import java.util.UUID;
import net.fabricmc.fabric.api.object.builder.v1.entity.FabricDefaultAttributeRegistry;
import net.minecraft.Bootstrap;
import net.minecraft.SharedConstants;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 実体の標準AI tickと身体の向き更新を同じ順で通し、pitchの巻き戻しを検出する。 */
class BodyOrientationTest {
    @BeforeAll static void bootstrap() {
        SharedConstants.createGameVersion(); Bootstrap.initialize();
        FabricDefaultAttributeRegistry.register(DogidoLab.DOGIDO,DogidoEntity.attributes());
    }
    @Test void playerAboveBelowAndEveryRelativeDirectionApplyInOneTickWhileMoving() {
        for (int direction=0; direction<8; direction++) for (double height : new double[]{-2.35,0,2.35}) {
            DogidoEntity body=new DogidoEntity(DogidoLab.DOGIDO,null);
            body.setYaw(173); body.setPitch(0);
            double angle=Math.toRadians(173+direction*45);
            Vec3d target=body.getEyePos().add(-10*Math.sin(angle),height,10*Math.cos(angle));
            var look=new DirectedLook(); UUID id=UUID.randomUUID();
            look.begin(id,new Position(target.x,target.y,target.z),0);
            // 移動中の実体に向きを適用しても、位置・速度はそのまま残る。
            body.setVelocity(.15,.03,-.1);var velocity=body.getVelocity();var position=body.getEntityPos();
            body.getLookControl().tick();body.applyDirectedLook(target);
            assertEquals(id,look.applied(0));assertEquals(velocity,body.getVelocity());assertEquals(position,body.getEntityPos());
            var expected=BodyVisionGeometry.facing(target.subtract(body.getEyePos()),0,0);
            assertEquals(0,MathHelper.wrapDegrees(expected.yaw()-body.getYaw()),1.0);
            assertEquals(expected.pitch(),body.getPitch(),1.0);
        }
    }
    @Test void idleAndFloatingKeepCompletedOrientationUntilAnActualMove() {
        DogidoEntity body=new DogidoEntity(DogidoLab.DOGIDO,null);
        body.setYaw(73); body.setPitch(27);
        for (int tick=0; tick<1200; tick++) {
            body.getLookControl().tick();
            body.updateOrientation(null,new Vec3d(0,.001,0));
            assertEquals(73,body.getYaw()); assertEquals(27,body.getPitch());
        }
        body.updateOrientation(null,new Vec3d(0,0,.1));
        assertEquals(67,body.getYaw()); assertEquals(23,body.getPitch());
    }
}
