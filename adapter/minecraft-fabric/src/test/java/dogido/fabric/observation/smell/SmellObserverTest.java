package dogido.fabric.observation.smell;

import java.util.List;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class SmellObserverTest {
    @Test void rainThenClearStartsOneWindowAndClearDoesNotExtendIt() {
        SmellObserver observer = new SmellObserver();
        observer.updateRainAfterSmellWindow("rain", 10);
        assertEquals(-1, observer.current().rainAfterWindowId());
        observer.updateRainAfterSmellWindow("clear", 20);
        assertEquals(3620, observer.current().rainAfterWindowId());
        observer.updateRainAfterSmellWindow("clear", 100);
        assertEquals(3620, observer.current().rainAfterWindowId());
    }

    @Test void thunderRainStartsWindowButSnowAndDryDoNot() {
        SmellObserver observer = new SmellObserver();
        observer.updateRainAfterSmellWindow("thunder_rain", 1);
        observer.updateRainAfterSmellWindow("clear", 2);
        assertEquals(3602, observer.current().rainAfterWindowId());
        for (String weather : List.of("snow", "thunder_snow", "dry", "thunder_dry")) {
            observer.updateRainAfterSmellWindow(weather, 3);
            assertEquals(-1, observer.current().rainAfterWindowId());
            observer.updateRainAfterSmellWindow("clear", 4);
            assertEquals(-1, observer.current().rainAfterWindowId());
        }
    }

    @Test void resetClearsWeatherHistoryWindowAndScanSchedule() {
        SmellObserver observer = new SmellObserver();
        observer.updateRainAfterSmellWindow("rain", 1);
        observer.updateRainAfterSmellWindow("clear", 2);
        observer.cacheBlockCandidates(List.of(), 10);
        assertFalse(observer.blockScanDue(11, 20));
        observer.reset();
        assertEquals(-1, observer.current().rainAfterWindowId());
        assertEquals("none", observer.current().observation().status());
        assertTrue(observer.blockScanDue(0, 20));
        observer.updateRainAfterSmellWindow("clear", 11);
        assertEquals(-1, observer.current().rainAfterWindowId());
    }

    @Test void blockScanUsesSharedTickAndConfiguredIntervalBoundary() {
        SmellObserver observer = new SmellObserver();
        assertTrue(observer.blockScanDue(100, 20));
        observer.cacheBlockCandidates(List.of(), 100);
        assertFalse(observer.blockScanDue(119, 20));
        assertTrue(observer.blockScanDue(120, 20));
        observer.cacheBlockCandidates(List.of(), 120);
        assertFalse(observer.blockScanDue(120, 20));
    }
}
