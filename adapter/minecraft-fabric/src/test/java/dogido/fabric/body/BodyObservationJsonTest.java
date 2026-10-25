package dogido.fabric.body;

import com.google.gson.JsonParser;
import dogido.fabric.protocol.EventJsonBuilder;
import dogido.lab.api.BodyProtocol;
import dogido.lab.api.BodyProtocol.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.net.URL;
import java.net.URLClassLoader;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** Java送信とRust受信が同じfixtureを使う。任意mod不在のclass loadingも検査する。 */
class BodyObservationJsonTest {
    static final UUID OWNER = UUID.fromString("11111111-1111-4111-8111-111111111111");
    static final String WORLD = "minecraft:overworld";
    static Snapshot snapshot(Presence presence, Position position, float yaw) {
        return snapshot(presence,position,yaw,
            new PoseObservation(new Position(10,65,20),new Position(10,64,23),360,0,450,15),
            new VisionObservation(100,16,120,90,Visibility.VISIBLE,"in_view"));
    }
    static Snapshot snapshot(Presence presence, Position position, float yaw, PoseObservation pose, VisionObservation vision) {
        return new Snapshot(BodyProtocol.VERSION,presence,new Context(OWNER,
            UUID.fromString("22222222-2222-4222-8222-222222222222"),UUID.fromString("33333333-3333-4333-8333-333333333333"),WORLD),
            WORLD,100,200,position,yaw,Activity.HOLDING,"test",0,null,null,Sensors.BODY_VISION_AND_HEARING,null,null,
            pose,vision,new HearingObservation(ObservationStatus.OBSERVED,100,java.util.List.of(),false),new ControlObservation(false,Mode.HOLD,16,24,new Reassurance(50,0,false,false,false)));
    }
    @Test void poseWireMatchesRustFixtureAndLeavesUncollectedSensesUnavailable() throws Exception {
        var actual = BodyObservationJson.observed(snapshot(Presence.PRESENT,new Position(10,64,20),360),25,OWNER,WORLD);
        var expected = JsonParser.parseString(Files.readString(Path.of("../../dogido-rust/fixtures/body-observation-v3.json"))).getAsJsonObject();
        assertEquals("not_sampled",actual.getAsJsonObject("sample").getAsJsonObject("senses").get("reason").getAsString());
        expected.getAsJsonObject("sample").add("senses",BodyObservationJson.unavailableSenses(100,"not_sampled"));
        assertEquals(expected,actual);
        assertFalse(actual.toString().contains("distance"));
    }
    @Test void eventEnvelopeKeepsAnIndependentBodyCopy() {
        var observation = BodyObservationJson.observed(snapshot(Presence.PRESENT,new Position(10,64,20),0),25,OWNER,WORLD);
        var event = new EventJsonBuilder("status_snapshot","system","background","high","2026-10-11T00:00:00Z",new dogido.fabric.observation.smell.SmellObserver().current())
            .bodyObservation(observation).finish();
        observation.getAsJsonObject("sample").addProperty("server_tick",999);
        observation.getAsJsonObject("sample").getAsJsonObject("vision").addProperty("reason","occluded");
        assertEquals("in_view",event.getAsJsonObject("body_observation").getAsJsonObject("sample").getAsJsonObject("vision").get("reason").getAsString());
        assertEquals(100,event.getAsJsonObject("body_observation").getAsJsonObject("sample").get("server_tick").getAsLong());
    }
    @Test void missingStaleWrongOwnerAndWrongWorldHaveNoCoordinates() {
        var s = snapshot(Presence.PRESENT,new Position(10,64,20),0);
        var legacy = new com.google.gson.Gson().toJsonTree(s).getAsJsonObject();
        legacy.addProperty("version",4);
        assertEquals("version_mismatch",BodyObservationJson.observed(
            new com.google.gson.Gson().fromJson(legacy,Snapshot.class),0,OWNER,WORLD).get("reason").getAsString());
        for (var actual : java.util.List.of(
                BodyObservationJson.observed(s,3000,OWNER,WORLD),
                BodyObservationJson.observed(s,0,UUID.randomUUID(),WORLD),
                BodyObservationJson.observed(s,0,OWNER,"minecraft:the_nether"),
                BodyObservationJson.observed(snapshot(Presence.UNLOADED,null,0),0,OWNER,WORLD),
                BodyObservationJson.observed(snapshot(Presence.PRESENT,new Position(Double.NaN,0,0),0),0,OWNER,WORLD))) {
            assertEquals("unavailable",actual.get("status").getAsString());
            assertFalse(actual.has("sample"));
        }
    }
    @Test void unknownVisionKeepsPoseAndInvalidVisionOrPoseIsRejected() {
        var s = snapshot(Presence.PRESENT,new Position(10,64,20),-540);
        var unknown = new VisionObservation(100,16,120,90,Visibility.UNAVAILABLE,"area_unloaded");
        var valid = BodyObservationJson.observed(snapshot(s.presence(),s.position(),s.yaw(),s.pose(),unknown),0,OWNER,WORLD);
        assertEquals("observed",valid.get("status").getAsString());
        assertEquals(-180,valid.getAsJsonObject("sample").getAsJsonObject("self_pose").get("yaw").getAsDouble());
        assertEquals("unavailable",valid.getAsJsonObject("sample").getAsJsonObject("vision").get("player_visibility").getAsString());
        assertTrue(valid.getAsJsonObject("sample").has("player_pose"));
        for (var bad : java.util.List.of(
                new VisionObservation(99,16,120,90,Visibility.VISIBLE,"in_view"),
                new VisionObservation(100,16,120,90,Visibility.VISIBLE,"occluded"),
                new VisionObservation(100,16,120,90,Visibility.UNAVAILABLE,"occluded"),
                new VisionObservation(100,Double.NaN,120,90,Visibility.VISIBLE,"in_view"),
                new VisionObservation(100,0,120,90,Visibility.VISIBLE,"in_view"),
                new VisionObservation(100,16,361,90,Visibility.VISIBLE,"in_view"),
                new VisionObservation(100,16,120,181,Visibility.VISIBLE,"in_view"))) {
            assertEquals("invalid_snapshot",BodyObservationJson.observed(
                snapshot(s.presence(),s.position(),0,s.pose(),bad),0,OWNER,WORLD).get("reason").getAsString());
        }
        for (var pose : java.util.List.of(
                new PoseObservation(s.pose().eyePosition(),s.pose().ownerPosition(),Float.NaN,0,0,0),
                new PoseObservation(s.pose().eyePosition(),s.pose().ownerPosition(),0,91,0,0),
                new PoseObservation(s.pose().eyePosition(),s.pose().ownerPosition(),0,0,Float.POSITIVE_INFINITY,0),
                new PoseObservation(s.pose().eyePosition(),s.pose().ownerPosition(),0,0,0,-91))) {
            assertEquals("invalid_snapshot",BodyObservationJson.observed(
                snapshot(s.presence(),s.position(),0,pose,s.vision()),0,OWNER,WORLD).get("reason").getAsString());
        }
    }
    @Test void optionalBoundaryLoadsWhenBodyApiClassesAreAbsent() throws Exception {
        URL code = BodyConnection.class.getProtectionDomain().getCodeSource().getLocation();
        try (var loader = new URLClassLoader(new URL[]{code},getClass().getClassLoader()) {
            @Override protected Class<?> loadClass(String name, boolean resolve) throws ClassNotFoundException {
                if (name.startsWith("dogido.lab.")) throw new ClassNotFoundException(name);
                if (name.startsWith("dogido.fabric.body.") || name.equals("dogido.fabric.DogidoClientAdapter")) {
                    Class<?> c = findLoadedClass(name);
                    if (c == null) c = findClass(name);
                    if (resolve) resolveClass(c);
                    return c;
                }
                return super.loadClass(name,resolve);
            }
        }) {
            var connection = loader.loadClass("dogido.fabric.body.BodyConnection");
            var instance = connection.getConstructor().newInstance();
            var absent = connection.getMethod("capture",UUID.class,String.class).invoke(instance,OWNER,WORLD);
            assertEquals("not_installed",JsonParser.parseString(absent.toString()).getAsJsonObject().get("reason").getAsString());
            var method = connection.getDeclaredMethod("unavailable",String.class);
            method.setAccessible(true);
            assertTrue(method.invoke(null,"not_installed").toString().contains("not_installed"));
            assertThrows(ClassNotFoundException.class,() -> loader.loadClass("dogido.lab.api.BodyBridge"));
            var settings = new dogido.fabric.observation.mobs.ThreatObserver.Settings(16,16,8,4,2,40,200);
            var collectorType = loader.loadClass("dogido.fabric.body.BodySensesCollector");
            var collector = collectorType.getConstructor(org.slf4j.Logger.class,java.util.function.LongSupplier.class,
                int.class,double.class,settings.getClass()).newInstance(org.slf4j.LoggerFactory.getLogger("absence-test"),
                    (java.util.function.LongSupplier)() -> 0L,10,16.0,settings);
            collectorType.getMethod("reset").invoke(collector);
            var runtimeType = loader.loadClass("dogido.fabric.body.BodyInspectRuntime");
            var submissionType = loader.loadClass("dogido.fabric.body.BodyInspectRuntime$Submission");
            var submission = java.lang.reflect.Proxy.newProxyInstance(loader,new Class<?>[]{submissionType},(proxy,call,args) -> "unavailable");
            var lookType = loader.loadClass("dogido.fabric.body.BodyInspectRuntime$LookSubmission");
            var lookSubmission = java.lang.reflect.Proxy.newProxyInstance(loader,new Class<?>[]{lookType},(proxy,call,args) -> "unavailable");
            var runtime = runtimeType.getConstructor(connection,collectorType,java.util.function.Consumer.class,java.util.function.Predicate.class,submissionType,lookType)
                .newInstance(instance,collector,(java.util.function.Consumer<com.google.gson.JsonObject>) value -> {},
                    (java.util.function.Predicate<dogido.fabric.protocol.DogidoEventClient.SessionRef>) ref -> false,submission,lookSubmission);
            runtimeType.getMethod("clear").invoke(runtime);
            assertNotNull(loader.loadClass("dogido.fabric.DogidoClientAdapter").getConstructor().newInstance());
        }
    }
}
