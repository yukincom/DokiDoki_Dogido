package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.lab.api.perception.BodyVisibility.Trace;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static dogido.fabric.body.BodyInspectProtocol.*;
import static org.junit.jupiter.api.Assertions.*;

/** 向き・境界・遮蔽と、配送受付/実到着/集中解除の時間をゲームやモデルなしで照合する。 */
class BodyAttentionTest {
    private static final Vec3d EYE=new Vec3d(10,64,20);
    private static final long SEQUENCE=101;
    private static JsonObject frame(long tick, String activity, String reason) {
        var row=BodyInspectControllerTest.observation();
        row.addProperty("server_tick",tick); row.addProperty("activity",activity); row.addProperty("reason",reason);
        return row;
    }
    private static void outcome(JsonObject row, Command command, String outcome, String reason) {
        var result=new JsonObject(); result.addProperty("action_id",command.commandId().toString());
        result.addProperty("sequence",SEQUENCE); result.addProperty("outcome",outcome); result.addProperty("reason",reason);
        row.add("action_result",result);
    }
    @Test void expandedWindowHasEightInFrontOneBehindTwoOnEitherSideAndThreeVertically() {
        var view=new BodyAttention.View(EYE,0,false,null);
        for (Vec3d delta : List.of(new Vec3d(0,0,8),new Vec3d(0,0,-1),new Vec3d(2,0,0),
                new Vec3d(-2,0,0),new Vec3d(0,3,0),new Vec3d(0,-3,0),new Vec3d(2,3,8)))
            assertTrue(view.contains(EYE.add(delta)),delta.toString());
        for (Vec3d delta : List.of(new Vec3d(0,0,8.001),new Vec3d(0,0,-1.001),new Vec3d(2.001,0,0),
                new Vec3d(-2.001,0,0),new Vec3d(0,3.001,0),new Vec3d(0,-3.001,0)))
            assertFalse(view.contains(EYE.add(delta)),delta.toString());
    }
    @Test void windowTurnsWithBodyYawIncludingDiagonalHeadings() {
        var west=new BodyAttention.View(EYE,90,false,null);
        assertTrue(west.contains(EYE.add(-8,3,2)));
        assertTrue(west.contains(EYE.add(1,-3,-2)));
        assertFalse(west.contains(EYE.add(1.001,0,0)));
        assertFalse(west.contains(EYE.add(0,0,2.001)));
        var diagonal=new BodyAttention.View(EYE,45,false,null);
        double d=8/Math.sqrt(2);
        assertTrue(diagonal.contains(EYE.add(-d,0,d)));
        assertFalse(diagonal.contains(EYE.add(-d-.001,0,d+.001)));
    }
    @Test void narrowWindowIsOneBlockInEveryDirection() {
        var view=new BodyAttention.View(EYE,0,true,null);
        assertTrue(view.contains(EYE.add(1,1,1)));
        assertTrue(view.contains(EYE.add(-1,-1,-1)));
        for (Vec3d delta : List.of(new Vec3d(1.001,0,0),new Vec3d(0,1.001,0),new Vec3d(0,0,1.001)))
            assertFalse(view.contains(EYE.add(delta)));
    }
    @Test void wallsAndUnavailableRaysHideTargetsAndOutsideTheWindowNeverUsesARay() {
        var view=new BodyAttention.View(EYE,0,false,null);
        var points=List.of(EYE.add(0,0,8));
        assertFalse(view.visible(points,16,p -> Trace.BLOCKED));
        assertFalse(view.visible(points,16,p -> Trace.UNAVAILABLE));
        assertTrue(view.visible(points,16,p -> Trace.CLEAR));
        AtomicInteger rays=new AtomicInteger();
        assertFalse(view.visible(List.of(EYE.add(0,0,8.001)),16,p -> { rays.incrementAndGet(); return Trace.CLEAR; }));
        assertEquals(0,rays.get());
        assertTrue(view.visible(List.of(EYE.add(0,0,2),EYE.add(0,1,2)),16,
            p -> p.y == EYE.y ? Trace.BLOCKED : Trace.CLEAR));
    }
    @Test void followAndSettlingNarrowTheWindowAndLookaroundAllowsSearching() {
        var attention=new BodyAttention();
        assertTrue(attention.view(EYE,0,frame(100,"following","distance_follow")).narrow());
        assertTrue(attention.view(EYE,0,frame(100,"holding","follow_settling")).narrow());
        assertFalse(attention.view(EYE,0,frame(100,"holding","follow_lookaround")).narrow());
        var disabled=frame(100,"holding","within_follow_distance"); disabled.addProperty("enabled",false);
        assertNull(attention.view(EYE,0,disabled));
    }
    @Test void queuedAndAcceptedFocusOnlyTheSelectedEntityAndArrivalKeepsFocusUntilBodyDwellEnds() {
        var attention=new BodyAttention(); var command=BodyInspectControllerTest.command();
        attention.focus(command,100,SEQUENCE,() -> true);
        var queued=frame(100,"holding","within_follow_distance"); attention.observe(queued);
        var view=attention.view(EYE,0,queued);
        assertEquals(command.target(),view.target());
        assertTrue(view.entity(command.target().entityId())); assertFalse(view.entity(UUID.randomUUID()));
        assertFalse(view.block(new BlockPos(0,64,0),"minecraft:stone"));
        var moving=frame(400,"moving","inspecting"); outcome(moving,command,"accepted","accepted");
        attention.observe(moving); assertNotNull(attention.view(EYE,0,moving).target());
        var arrived=frame(590,"holding","inspection_dwell"); outcome(arrived,command,"succeeded","arrived");
        attention.observe(arrived); assertNotNull(attention.view(EYE,0,arrived).target());
        arrived.addProperty("server_tick",689); attention.observe(arrived);
        assertNotNull(attention.view(EYE,0,arrived).target());
        arrived.addProperty("server_tick",690); arrived.addProperty("reason","follow_lookaround"); attention.observe(arrived);
        assertNull(attention.view(EYE,0,arrived).target());
    }
    @Test void lateArrivalReceiptDoesNotStartAnotherFiveSeconds() {
        var attention=new BodyAttention(); var command=BodyInspectControllerTest.command(); attention.focus(command,100,SEQUENCE,() -> true);
        var row=frame(400,"holding","within_follow_distance"); outcome(row,command,"succeeded","arrived");
        attention.observe(row); assertNull(attention.view(EYE,0,row).target());
    }
    @Test void focusedBlockUsesExactCellAndStillRequiresLineOfSight() {
        var target=new Target("b:10,64,22:minecraft:coal_ore","block","minecraft:coal_ore","石炭鉱石",new Point(10.5,64.5,22.5),null);
        var view=new BodyAttention.View(EYE,180,false,target);
        assertTrue(view.block(new BlockPos(10,64,22),"minecraft:coal_ore")); assertFalse(view.block(new BlockPos(10,64,23),"minecraft:coal_ore"));
        assertFalse(view.entity(UUID.randomUUID()));
        assertFalse(view.block(new BlockPos(10,64,22),"minecraft:stone"));
        assertFalse(view.visible(List.of(new Vec3d(10.5,64.5,22.5)),21,p -> Trace.BLOCKED));
    }
    @Test void priorityFailureExpiryOrGenerationChangeClearFocus() {
        for (String change : List.of("following","calling","retreating","settling","disabled","mode","context","expired","stale","failed","sequence")) {
            var attention=new BodyAttention(); var command=BodyInspectControllerTest.command(); attention.focus(command,100,SEQUENCE,() -> true);
            var row=frame(110,"holding","inspection_dwell"); outcome(row,command,"succeeded","arrived");
            switch (change) {
                case "following","calling","retreating" -> row.addProperty("activity",change);
                case "settling" -> row.addProperty("reason","follow_settling");
                case "disabled" -> row.addProperty("enabled",false);
                case "mode" -> row.addProperty("mode","hold");
                case "context" -> row.getAsJsonObject("context").addProperty("epoch",UUID.randomUUID().toString());
                case "expired" -> row.addProperty("server_tick",900);
                case "stale" -> row.addProperty("age_ms",3000);
                case "failed" -> outcome(row,command,"rejected","inspection_target_unsafe");
                case "sequence" -> row.getAsJsonObject("action_result").addProperty("sequence",SEQUENCE+1);
            }
            attention.observe(row);
            var view=attention.view(EYE,0,row); assertTrue(view == null || view.target() == null,change);
        }
    }
    @Test void adapterTimeoutClearsOnlyTheMatchingFocusAndSuccessWaitsForTheBody() {
        var attention=new BodyAttention(); var command=BodyInspectControllerTest.command(); attention.focus(command,100,SEQUENCE,() -> true);
        assertFalse(attention.result(result(BodyInspectControllerTest.command(),"expired","result_timeout",BodyInspectControllerTest.NOW)));
        assertFalse(attention.result(result(command,"succeeded","arrived",BodyInspectControllerTest.NOW)));
        assertNotNull(attention.view(EYE,0,frame(101,"holding","inspection_dwell")).target());
        assertTrue(attention.result(result(command,"expired","result_timeout",BodyInspectControllerTest.NOW)));
        assertNull(attention.view(EYE,0,frame(102,"holding","within_follow_distance")).target());
    }
    @Test void httpSessionLossClearsMovingAndAlreadyArrivedFocusEvenInTheSameBodyFrame() {
        for (boolean arrived : List.of(false,true)) {
            var current=new java.util.concurrent.atomic.AtomicBoolean(true);
            var attention=new BodyAttention(); var command=BodyInspectControllerTest.command();
            attention.focus(command,100,SEQUENCE,current::get);
            var row=frame(200,arrived ? "holding" : "moving",arrived ? "inspection_dwell" : "inspecting");
            outcome(row,command,arrived ? "succeeded" : "accepted",arrived ? "arrived" : "accepted");
            assertFalse(attention.observe(row)); assertNotNull(attention.view(EYE,0,row).target());
            current.set(false);
            assertTrue(attention.observe(row),"same frame must invalidate its cache");
            assertNull(attention.view(EYE,0,row).target());
            assertFalse(attention.observe(row));
        }
    }
    @Test void reassuranceContinuouslyWidensOnlyTheSafeAmbientWindow() {
        var attention=new BodyAttention(); var row=frame(100,"holding","follow_lookaround");
        for (double value : List.of(0.0,25.0,50.0,100.0)) {
            row.getAsJsonObject("reassurance").addProperty("value",value);
            var view=attention.view(EYE,0,row); double expansion=value/100;
            assertTrue(view.contains(EYE.add(0,0,1+7*expansion)));
            assertFalse(view.contains(EYE.add(0,0,1.001+7*expansion)));
            assertTrue(view.contains(EYE.add(1+expansion,1+2*expansion,0)));
            assertEquals(expansion,attention.observation(row).get("expansion").getAsDouble());
            row.addProperty("activity","following"); assertTrue(attention.view(EYE,0,row).narrow());
            row.addProperty("activity","holding");
        }
    }
    @Test void ownerApproachAndDwellRemainNarrowWithoutAFalseTargetFocus() {
        var attention=new BodyAttention();
        var command=new Command(UUID.randomUUID(),BodyInspectControllerTest.NOW,BodyInspectControllerTest.NOW.plusSeconds(4),
            BodyInspectControllerTest.CONTEXT,null,Purpose.OWNER_FACE);
        attention.focus(command,100,SEQUENCE,() -> true);
        var moving=frame(110,"moving","owner_face"); outcome(moving,command,"accepted","accepted"); attention.observe(moving);
        assertTrue(attention.view(EYE,0,moving).narrow()); assertNull(attention.view(EYE,0,moving).target());
        var arrived=frame(130,"holding","owner_face_dwell"); outcome(arrived,command,"succeeded","arrived"); attention.observe(arrived);
        assertTrue(attention.view(EYE,0,arrived).narrow()); assertEquals("narrow",attention.observation(arrived).get("scope").getAsString());
        arrived.addProperty("reason","follow_lookaround"); attention.observe(arrived);
        assertFalse(attention.view(EYE,0,arrived).narrow());
    }
}
