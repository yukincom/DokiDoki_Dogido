package dogido.fabric.screenshot;

import java.io.PrintWriter;
import org.junit.platform.engine.discovery.DiscoverySelectors;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;
import org.junit.platform.launcher.listeners.SummaryGeneratingListener;

/** 見込み: 既存JUnitで選択した比較試験を実行すれば、失敗・未実行を区別できる。
 * 合格: 対象テストを1件以上実行し、失敗・中断・skipが0。範囲: BodyViewChangeTestだけ。Gradle全体・Minecraft起動は確認しない。
 */
public final class BodyViewJUnitRunner {
    public static void main(String[] args) {
        var request = LauncherDiscoveryRequestBuilder.request()
            .selectors(DiscoverySelectors.selectClass(BodyViewChangeTest.class)).build();
        var listener = new SummaryGeneratingListener();
        var launcher = LauncherFactory.create();
        launcher.registerTestExecutionListeners(listener);
        launcher.execute(request);
        var summary = listener.getSummary();
        var out = new PrintWriter(System.out, true);
        summary.printTo(out); summary.printFailuresTo(out);
        if (summary.getTestsFoundCount() == 0 || summary.getTestsSucceededCount() != summary.getTestsFoundCount()
                || summary.getTestsSkippedCount() != 0 || summary.getTestsAbortedCount() != 0 || summary.getTotalFailureCount() != 0)
            throw new AssertionError("JUnit image checks did not all pass");
    }
}
