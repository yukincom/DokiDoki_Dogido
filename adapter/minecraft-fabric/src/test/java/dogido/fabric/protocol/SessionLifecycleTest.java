package dogido.fabric.protocol;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Set;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 実HTTPで退出・再入場と登録中の退出を再現し、旧接続だけが解放されることを確認する。 */
class SessionLifecycleTest {
    private static void await(BooleanSupplier condition) throws Exception {
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (!condition.getAsBoolean() && System.nanoTime() < until) Thread.sleep(10);
        assertTrue(condition.getAsBoolean(), "session lifecycle did not settle");
    }

    private static final class Fixture implements AutoCloseable {
        final HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        final ExecutorService executor = Executors.newCachedThreadPool();
        final Set<String> active = ConcurrentHashMap.newKeySet();
        final AtomicInteger created = new AtomicInteger();
        final AtomicInteger unauthorized = new AtomicInteger();
        final CountDownLatch releaseRegistration = new CountDownLatch(1);
        final DogidoEventClient client;

        Fixture(boolean delayFirst) throws Exception {
            server.setExecutor(executor);
            server.createContext("/api/v1/adapter-sessions", exchange -> {
                int status = 200;
                String response = "{}";
                if (!"Bearer test-token".equals(exchange.getRequestHeaders().getFirst("Authorization"))) {
                    unauthorized.incrementAndGet();
                    status = 401;
                } else if (exchange.getRequestMethod().equals("POST")) {
                    exchange.getRequestBody().readAllBytes();
                    if (active.size() >= 8) status = 429;
                    else {
                        String id = "session-" + created.incrementAndGet();
                        active.add(id);
                        if (delayFirst && id.equals("session-1")) {
                            try { releaseRegistration.await(5, TimeUnit.SECONDS); }
                            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
                        }
                        response = "{\"session_id\":\"" + id + "\"}";
                        status = 201;
                    }
                } else if (exchange.getRequestMethod().equals("DELETE")) {
                    String path = exchange.getRequestURI().getPath();
                    active.remove(path.substring(path.lastIndexOf('/') + 1));
                }
                byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(status, bytes.length);
                exchange.getResponseBody().write(bytes);
                exchange.close();
            });
            server.start();
            client = new DogidoEventClient(org.slf4j.LoggerFactory.getLogger("session-lifecycle-test"),
                new DogidoEventClient.ConnectionSettings(true,
                    "http://127.0.0.1:" + server.getAddress().getPort(), "test-token"),
                command -> {}, (command, ref) -> {}, () -> false, null, null, null, null, null);
        }
        public void close() {
            releaseRegistration.countDown();
            server.stop(0);
            executor.shutdownNow();
        }
    }

    @Test void repeatedRejoinReleasesEachConnectionBeforeReachingTheSessionLimit() throws Exception {
        try (var f = new Fixture(false)) {
            for (int i = 0; i < 10; i++) {
                f.client.ensureSession("player");
                await(() -> f.client.currentSession() != null);
                assertEquals(1, f.active.size());
                f.client.disconnected();
                assertNull(f.client.currentSession());
                await(f.active::isEmpty);
            }
            assertEquals(10, f.created.get());
            assertEquals(0, f.unauthorized.get());
        }
    }

    @Test void exitBeforeRegistrationResponseReleasesOnlyTheOldConnection() throws Exception {
        try (var f = new Fixture(true)) {
            f.client.ensureSession("player");
            await(() -> f.active.size() == 1);
            f.client.disconnected();
            f.releaseRegistration.countDown();
            await(f.active::isEmpty);
            assertNull(f.client.currentSession());
            await(() -> { f.client.ensureSession("player"); return f.client.currentSession() != null; });
            assertEquals("session-2", f.client.currentSession().sessionId());
            assertEquals(Set.of("session-2"), f.active);
            f.client.disconnected();
            await(f.active::isEmpty);
            assertEquals(0, f.unauthorized.get());
        }
    }
}
