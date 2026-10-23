package dogido.fabric.protocol;

import dogido.fabric.body.BodyConnection;
import dogido.fabric.body.BodyInspectProtocol;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** HTTP callbackと同じsession monitorを通し、世代切替と身体queueが途中で交差しないことを検証する。 */
class BodyInspectSessionGuardTest {
    static DogidoEventClient client() throws Exception {
        var client=new DogidoEventClient(org.slf4j.LoggerFactory.getLogger("body-session-test"),
            new DogidoEventClient.ConnectionSettings(true,"http://127.0.0.1:1",""),command -> {},(command,ref) -> {},() -> false,null,null,null,null,null);
        var field=DogidoEventClient.class.getDeclaredField("sessionId"); field.setAccessible(true); field.set(client,"session");
        return client;
    }
    static BodyInspectProtocol.Command command(Instant issued) {
        var context=new BodyInspectProtocol.Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        var entity=UUID.randomUUID();
        return new BodyInspectProtocol.Command(UUID.randomUUID(),issued,issued.plusSeconds(5),context,
            new BodyInspectProtocol.Target("e:"+entity,"entity","minecraft:cow","Cow",new BodyInspectProtocol.Point(8,64,0),entity));
    }
    static BodyConnection.MoveRequest request(BodyInspectProtocol.Command command) {
        return new BodyConnection.MoveRequest(command.context(),101,command.commandId(),100,100,new BodyInspectProtocol.Point(6,64,0),BodyInspectProtocol.Purpose.INSPECT);
    }
    @Test void staleSessionAndExpiredCommandNeverReachTheBodyQueue() throws Exception {
        var client=client(); var ref=client.currentSession(); var calls=new AtomicInteger();
        var expired=command(Instant.now().minusSeconds(10));
        assertEquals("expired",client.submitBodyInspect(ref,expired,request(expired),move -> { calls.incrementAndGet(); return "queued"; }));
        var fresh=command(Instant.now()); client.invalidateDisplayRequests();
        assertEquals("stale_session",client.submitBodyInspect(ref,fresh,request(fresh),move -> { calls.incrementAndGet(); return "queued"; }));
        assertEquals(0,calls.get());
    }
    @Test void worldEpochMutationWaitsUntilTheAcceptedQueueCompletes() throws Exception {
        var client=client(); var ref=client.currentSession(); var command=command(Instant.now());
        var started=new CountDownLatch(1); var changed=new CountDownLatch(1);
        try (var executor=Executors.newSingleThreadExecutor()) {
            String result=client.submitBodyInspect(ref,command,request(command),move -> {
                executor.execute(() -> { started.countDown(); client.invalidateDisplayRequests(); changed.countDown(); });
                try {
                    assertTrue(started.await(2,TimeUnit.SECONDS));
                    assertFalse(changed.await(100,TimeUnit.MILLISECONDS));
                    assertTrue(client.isCurrent(ref));
                } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
                return "queued";
            });
            assertEquals("queued",result); assertTrue(changed.await(2,TimeUnit.SECONDS)); assertFalse(client.isCurrent(ref));
        }
    }
    @Test void mismatchedActionIdentityNeverReachesTheBodyQueue() throws Exception {
        var client=client(); var command=command(Instant.now()); var request=request(command); var calls=new AtomicInteger();
        var mismatch=new BodyConnection.MoveRequest(request.context(),request.sequence(),UUID.randomUUID(),request.observedServerTick(),request.validForTicks(),request.destination(),request.purpose());
        assertEquals("invalid_action",client.submitBodyInspect(client.currentSession(),command,mismatch,move -> { calls.incrementAndGet(); return "queued"; }));
        assertEquals(0,calls.get());
    }
}
