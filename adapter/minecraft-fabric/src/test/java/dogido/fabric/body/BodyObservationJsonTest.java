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
        return new Snapshot(BodyProtocol.VERSION,presence,new Context(OWNER,
            UUID.fromString("22222222-2222-4222-8222-222222222222"),UUID.fromString("33333333-3333-4333-8333-333333333333"),WORLD),
            WORLD,100,200,position,yaw,Activity.HOLDING,"test",0,null,null,Sensors.NOT_IMPLEMENTED,null,null,
            new PoseObservation(new Position(10,65,20),new Position(10,64,23)));
    }
    @Test void wireMatchesRustFixtureAndDoesNotAlterRawPosition() throws Exception {
        var actual = BodyObservationJson.observed(snapshot(Presence.PRESENT,new Position(10,64,20),360),25,OWNER,WORLD);
        var expected = JsonParser.parseString(Files.readString(Path.of("../../dogido-rust/fixtures/body-observation-v1.json")));
        assertEquals(expected,actual);
        assertFalse(actual.toString().contains("distance"));
    }
    @Test void eventEnvelopeKeepsAnIndependentBodyCopy() {
        var observation = BodyObservationJson.observed(snapshot(Presence.PRESENT,new Position(10,64,20),0),25,OWNER,WORLD);
        var event = new EventJsonBuilder("status_snapshot","system","background","high","2026-10-11T00:00:00Z",new dogido.fabric.observation.smell.SmellObserver().current())
            .bodyObservation(observation).finish();
        observation.getAsJsonObject("sample").addProperty("server_tick",999);
        assertEquals(100,event.getAsJsonObject("body_observation").getAsJsonObject("sample").get("server_tick").getAsLong());
    }
    @Test void missingStaleWrongOwnerAndWrongWorldHaveNoCoordinates() {
        var s = snapshot(Presence.PRESENT,new Position(10,64,20),0);
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
    @Test void optionalBoundaryLoadsWhenBodyApiClassesAreAbsent() throws Exception {
        URL code = BodyConnection.class.getProtectionDomain().getCodeSource().getLocation();
        try (var loader = new URLClassLoader(new URL[]{code},getClass().getClassLoader()) {
            @Override protected Class<?> loadClass(String name, boolean resolve) throws ClassNotFoundException {
                if (name.startsWith("dogido.lab.")) throw new ClassNotFoundException(name);
                if (name.startsWith("dogido.fabric.body.")) {
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
        }
    }
}
