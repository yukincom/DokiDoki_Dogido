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
    @Test void shortCallReportsArrivalOnceAndFinishesTurningBeforeOrdinaryGazeResumes() {
        var body=new DogidoEntity(DogidoLab.DOGIDO,null);
        var controller=new dogido.lab.control.BodyController(1);
        var owner=new dogido.lab.control.BodyController.Owner(Vec3d.ZERO,0,1.1);
        var tuning=new dogido.lab.control.BodyTuning();
        var look=new DirectedLook();look.beginCall();controller.call(UUID.randomUUID(),owner,0);
        var destination=controller.plan(new Vec3d(0,1.1,3),owner,0,true,
            dogido.lab.api.BodyProtocol.Mode.FOLLOW,false,tuning,(p,w)->true).target();
        body.setPosition(destination);body.setYaw(0);body.setPitch(30);
        var arrived=controller.plan(destination,owner,1,true,
            dogido.lab.api.BodyProtocol.Mode.FOLLOW,false,tuning,(p,w)->true);
        assertEquals(dogido.lab.api.BodyProtocol.Activity.HOLDING,arrived.activity());
        assertTrue(controller.takeArrived());assertFalse(controller.takeArrived());assertFalse(controller.calling());
        Vec3d ownerEyes=new Vec3d(0,1.62,0),ordinary=body.getEyePos().add(0,0,8);
        look.arrived(ownerEyes);int turns=0;
        for(;turns<100 && look.arriving();turns++) {
            // 到着後も普段の上下浮遊を継続する。反対向きの通常注視は回頭後まで適用しない。
            body.setPosition(destination.add(0,.01*Math.sin(turns*.1),0));
            var before=body.getEntityPos();float yaw=body.getYaw(),pitch=body.getPitch();
            assertNull(body.updateLookOrientation(look,ordinary,Vec3d.ZERO,turns+1));
            assertEquals(before,body.getEntityPos());assertTrue(Math.abs(MathHelper.wrapDegrees(body.getYaw()-yaw))<=6.001);
            assertTrue(Math.abs(body.getPitch()-pitch)<=4.001);assertFalse(controller.takeArrived());
            if(turns==0)assertTrue(look.arriving(),"短距離の到着通知後も回頭は続く");
        }
        assertTrue(turns>1 && turns<100);assertFalse(look.arriving());
        var expected=BodyVisionGeometry.facing(ownerEyes.subtract(body.getEyePos()),0,0);
        assertEquals(0,MathHelper.wrapDegrees(expected.yaw()-body.getYaw()),.001);
        assertEquals(expected.pitch(),body.getPitch(),.001);
        float facingOwner=body.getYaw();body.updateLookOrientation(look,ordinary,Vec3d.ZERO,101);
        assertNotEquals(facingOwner,body.getYaw(),"回頭後は通常注視へ戻る");
    }
    @Test void newerLookImmediatelyReplacesArrivalTurnWithoutChangingMovement() {
        var body=new DogidoEntity(DogidoLab.DOGIDO,null);var look=new DirectedLook();
        look.beginCall();look.arrived(body.getEyePos().add(0,0,-8));
        body.updateLookOrientation(look,null,Vec3d.ZERO,1);assertTrue(look.arriving());
        Vec3d point=body.getEyePos().add(8,3,0);UUID id=UUID.randomUUID();
        look.begin(id,new Position(point.x,point.y,point.z),2);
        body.setVelocity(.1,.01,-.05);var position=body.getEntityPos();var velocity=body.getVelocity();
        assertEquals(id,body.updateLookOrientation(look,null,new Vec3d(.1,0,0),2));
        assertFalse(look.arriving());assertEquals(position,body.getEntityPos());assertEquals(velocity,body.getVelocity());
        var expected=BodyVisionGeometry.facing(point.subtract(body.getEyePos()),0,0);
        assertEquals(expected.yaw(),body.getYaw(),.001);assertEquals(expected.pitch(),body.getPitch(),.001);
        look.tick(102);look.arrived(body.getEyePos().add(0,0,-8));assertFalse(look.arriving());
    }
    @Test void callWithNoNewLookFacesItsActualDetourAndDoesNotRestoreOldLook() {
        var body=new DogidoEntity(DogidoLab.DOGIDO,null);var look=new DirectedLook();
        look.begin(UUID.randomUUID(),new Position(0,8,8),0);
        body.updateLookOrientation(look,null,Vec3d.ZERO,0);look.beginCall();
        body.setYaw(0);body.setPitch(0);
        body.updateLookOrientation(look,null,new Vec3d(.1,0,0),1);
        assertEquals(-6,body.getYaw());assertEquals(0,body.getPitch());assertFalse(look.active());
        body.updateLookOrientation(look,null,new Vec3d(0,.01,0),2);
        assertEquals(-6,body.getYaw());assertEquals(0,body.getPitch());
    }
}
