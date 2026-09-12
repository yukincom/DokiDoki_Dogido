package dogido.fabric;

import java.util.List;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.font.TextRenderer;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.render.RenderTickCounter;
import net.minecraft.client.sound.PositionedSoundInstance;
import net.minecraft.sound.SoundEvents;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.literal;

/** A read-only scroll. Voice editing stays on the server; this HUD never sends edits. */
final class DogidoWorkshopHud {
    private final WorkshopDisplayState state = new WorkshopDisplayState();
    private boolean enabled = true;
    private boolean sound = true;
    private boolean motion = true;

    static DogidoWorkshopHud register() {
        DogidoWorkshopHud hud = new DogidoWorkshopHud();
        HudElementRegistry.attachElementBefore(VanillaHudElements.CHAT,
            Identifier.of("dogido", "workshop_scroll"), hud::render);
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) ->
            dispatcher.register(literal("dogidoscroll")
                .executes(c -> { c.getSource().sendFeedback(Text.literal(
                    "掛け軸: /dogidoscroll hide|show / sound on|off / motion on|off（この起動中のみ）")); return 1; })
                .then(literal("hide").executes(c -> { hud.enabled = false; return 1; }))
                .then(literal("show").executes(c -> { hud.enabled = true; return 1; }))
                .then(literal("sound")
                    .then(literal("off").executes(c -> { hud.sound = false; return 1; }))
                    .then(literal("on").executes(c -> { hud.sound = true; return 1; })))
                .then(literal("motion")
                    .then(literal("off").executes(c -> { hud.motion = false; return 1; }))
                    .then(literal("on").executes(c -> { hud.motion = true; return 1; })))));
        return hud;
    }

    static long now() { return System.nanoTime() / 1_000_000; }
    void reset() { state.reset(); }
    void synchronizeAfter(long sequence) { state.synchronizeAfter(sequence); }
    void danger(boolean danger, boolean changed, long sequence) { state.danger(danger, changed, sequence, now()); }
    void receive(WorkshopDisplayState.Snapshot snapshot) {
        boolean cue = state.receive(snapshot, now());
        MinecraftClient client = MinecraftClient.getInstance();
        if (cue && enabled && sound && client.world != null && client.currentScreen == null && !client.options.hudHidden) {
            client.getSoundManager().play(PositionedSoundInstance.ui(SoundEvents.BLOCK_NOTE_BLOCK_HAT.value(), 1.8f, 0.15f));
        }
    }

    private static int color(int rgb, float alpha) {
        return Math.clamp(Math.round(alpha * 255), 0, 255) << 24 | rgb;
    }

    private void render(DrawContext context, RenderTickCounter tickCounter) {
        MinecraftClient client = MinecraftClient.getInstance();
        long now = now();
        state.update(now);
        float opacity = motion ? state.opacity(now) : state.showing() ? 1 : 0;
        var snapshot = state.displayed();
        if (!enabled || opacity < 0.01f || snapshot == null || client.world == null || client.player == null
                || client.currentScreen != null || client.options.hudHidden) return;
        int sw = context.getScaledWindowWidth(), sh = context.getScaledWindowHeight();
        int width = Math.max(1, Math.round(sw * .265f)), height = Math.max(1, Math.round(sh * .74f));
        int x = Math.round(sw * .985f) - width, y = Math.round(sh * .895f) - height;
        int inset = Math.max(1, Math.round(width * .02f));
        int top = Math.max(1, Math.round(height * .14f)), bottom = Math.max(1, Math.round(height * .09f));
        int px = x + Math.round(width * .06f), py = y + top;
        int pw = width - Math.round(width * .12f), ph = height - top - bottom;
        context.fill(x + inset, y, x + width - inset, y + height, color(0xc3c1ad, opacity));
        context.fill(x + inset, py, x + width - inset, py + ph, color(0xaaa993, opacity));
        context.fill(px, py, px + pw, py + ph, color(0xf5f0de, opacity));
        context.fill(x + inset, y, x + width - inset, y + 1, color(0x494335, opacity));
        context.fill(x, y + height - 1, x + width, y + height + 1, color(0x494335, opacity));

        List<String> lines = snapshot.shownLines();
        float glyph = sw * .035f;
        for (int i = 0; i < 3; i++) {
            int count = lines.get(i).codePointCount(0, lines.get(i).length());
            glyph = Math.min(glyph, (ph - width * .12f) / (count * 1.04f + i * .7f));
        }
        glyph = Math.max(1, Math.min(glyph, pw / 4f));
        for (int i = 0; i < 3; i++) {
            String line = lines.get(i);
            float cx = px + pw * (2.5f - i) / 3f;
            float cy = py + width * .06f + glyph * (i == 0 ? 0 : i == 1 ? .65f : 1.45f);
            boolean selected = snapshot.editing() && state.showing() && snapshot.selectedLine() != null && snapshot.selectedLine() == i;
            if (selected) {
                int left = Math.round(cx - glyph * .66f), right = Math.round(cx + glyph * .66f);
                int upper = Math.round(cy - glyph * .15f), lower = Math.round(cy + glyph * 1.04f * line.codePointCount(0, line.length()));
                context.fill(left, upper, right, lower, color(0x51341d, opacity));
                context.fill(left + 1, upper + 1, right - 1, lower - 1, color(0xf4dab0, opacity));
            }
            for (int cp : line.codePoints().toArray()) {
                drawCentered(context, client.textRenderer, new String(Character.toChars(cp)), cx, cy, glyph, color(0x27291f, opacity));
                cy += glyph * 1.04f;
            }
        }
        if (snapshot.editing() && state.showing()) {
            for (int i = 0; i < 3; i++) {
                int bx = x + Math.round(width * (.12f + i * .265f));
                int bw = Math.round(width * .23f);
                boolean selected = snapshot.selectedLine() != null && snapshot.selectedLine() == i;
                context.fill(bx, y + 2, bx + bw, y + top - 2, color(selected ? 0x51341d : 0x6d6854, opacity));
                context.fill(bx + 1, y + 3, bx + bw - 1, y + top - 3, color(selected ? 0xe99b36 : 0xfaf5e5, opacity));
                drawCentered(context, client.textRenderer, List.of("上", "中", "下").get(i), bx + bw / 2f,
                    y + top * .17f, top * .66f, color(0x302b21, opacity));
            }
        }
        String caption = snapshot.pending().isEmpty() ? "" : "未採用案";
        if (snapshot.editing() && state.showing()) {
            String selection = snapshot.selectedLine() == null ? "直す場所" : List.of("上", "中", "下").get(snapshot.selectedLine()) + "の句 選択中";
            caption = caption.isEmpty() ? selection : caption + "・" + selection;
        }
        if (!caption.isEmpty()) {
            float size = Math.min(bottom * .62f, width * .87f * client.textRenderer.fontHeight / Math.max(1, client.textRenderer.getWidth(caption)));
            drawCentered(context, client.textRenderer, caption, x + width / 2f, y + height - bottom * .8f, size, color(0x302b21, opacity));
        }
    }

    private static void drawCentered(DrawContext context, TextRenderer renderer, String text, float cx, float y, float height, int color) {
        float scale = height / renderer.fontHeight;
        context.getMatrices().pushMatrix();
        context.getMatrices().translate(cx, y);
        context.getMatrices().scale(scale, scale);
        context.drawText(renderer, text, -renderer.getWidth(text) / 2, 0, color, false);
        context.getMatrices().popMatrix();
    }
}
