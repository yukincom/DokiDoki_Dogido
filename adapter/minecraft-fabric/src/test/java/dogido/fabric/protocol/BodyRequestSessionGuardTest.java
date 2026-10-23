package dogido.fabric.protocol;

import dogido.fabric.body.BodyRequestProtocol;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 入力世代を返すBERT契約だけを受け、旧分類・旧世代へ戻らない。 */
class BodyRequestSessionGuardTest {
    private DogidoEventClient client() throws Exception { return BodyInspectSessionGuardTest.client(); }
    private void generation(DogidoEventClient client,DogidoEventClient.SessionRef ref,long value) throws Exception {
        var method=DogidoEventClient.class.getDeclaredMethod("handleAcceptedResponse",String.class,DogidoEventClient.SessionRef.class);
        method.setAccessible(true); method.invoke(client,"{\"body_request_generation\":"+value+",\"commands\":[]}",ref);
    }
    private BodyRequestProtocol.Command command(long generation,Instant issued) {
        return new BodyRequestProtocol.Command(UUID.randomUUID(),issued,issued.plusSeconds(5),generation,
            UUID.randomUUID(),"minecraft:overworld",BodyRequestProtocol.Mode.RECALL,null);
    }
    @Test void currentGenerationQueuesOnceAndNewInputInvalidatesTheOldCallback() throws Exception {
        var client=client(); var ref=client.currentSession(); var calls=new AtomicInteger();
        var old=command(10,Instant.now());
        assertEquals("stale_input_generation",client.submitBodyRequest(ref,old,() -> { calls.incrementAndGet(); return "queued"; }));
        generation(client,ref,10);
        assertEquals("queued",client.submitBodyRequest(ref,old,() -> { calls.incrementAndGet(); return "queued"; }));
        generation(client,ref,11);
        assertEquals("stale_input_generation",client.submitBodyRequest(ref,old,() -> { calls.incrementAndGet(); return "queued"; }));
        generation(client,ref,9); assertTrue(client.isCurrentBodyRequest(ref,11));
        assertEquals(1,calls.get());
    }
    @Test void oldWorldAndExpiredCommandsCannotCallTheSender() throws Exception {
        var client=client(); var ref=client.currentSession(); generation(client,ref,3); var calls=new AtomicInteger();
        assertEquals("expired",client.submitBodyRequest(ref,command(3,Instant.now().minusSeconds(10)),() -> { calls.incrementAndGet(); return "queued"; }));
        client.invalidateDisplayRequests();
        assertEquals("stale_session",client.submitBodyRequest(ref,command(3,Instant.now()),() -> { calls.incrementAndGet(); return "queued"; }));
        assertFalse(client.isCurrentBodyRequest(client.currentSession(),3)); assertEquals(0,calls.get());
    }
    @Test void generationMutationAndQueueUseTheSameMonitor() throws Exception {
        var client=client(); var ref=client.currentSession(); generation(client,ref,7);
        var started=new CountDownLatch(1); var changed=new CountDownLatch(1);
        try (var executor=Executors.newSingleThreadExecutor()) {
            assertEquals("queued",client.submitBodyRequest(ref,command(7,Instant.now()),() -> {
                executor.execute(() -> {
                    started.countDown();
                    try { generation(client,ref,8); } catch (Exception error) { throw new AssertionError(error); }
                    changed.countDown();
                });
                try {
                    assertTrue(started.await(2,TimeUnit.SECONDS)); assertFalse(changed.await(100,TimeUnit.MILLISECONDS));
                } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
                return "queued";
            }));
            assertTrue(changed.await(2,TimeUnit.SECONDS)); assertTrue(client.isCurrentBodyRequest(ref,8));
        }
    }
}
