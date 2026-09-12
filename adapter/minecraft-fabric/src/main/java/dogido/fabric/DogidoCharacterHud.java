package dogido.fabric;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

import com.mojang.brigadier.arguments.IntegerArgumentType;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gl.RenderPipelines;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.render.RenderTickCounter;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import static net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.argument;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.literal;

/** Client-only placement study; no server connection or conversation state needed. */
final class DogidoCharacterHud {
    private static final Logger LOGGER = LoggerFactory.getLogger("dogido-character-hud");
    private static final Identifier TEXTURE = Identifier.of("dogido", "textures/gui/character.png");
    private final Path configPath = FabricLoader.getInstance().getConfigDir().resolve("dogido-character.properties");
    private boolean visible = true;
    private int width = 96;
    private int right = 12;
    private int bottom = 36;

    static void register() {
        DogidoCharacterHud hud = new DogidoCharacterHud();
        hud.load();
        // Inherit F1 hiding; vanilla chat and accessibility subtitles stay above the character.
        HudElementRegistry.attachElementBefore(VanillaHudElements.CHAT,
            Identifier.of("dogido", "character"), hud::render);
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) ->
            dispatcher.register(literal("dogidohud")
                .executes(context -> {
                    context.getSource().sendFeedback(Text.literal(
                        "ドギド表示: /dogidohud size 96（幅）・offset 12 36（右・下の余白）・hide / show・reset"));
                    return 1;
                })
                .then(literal("hide").executes(context -> { hud.visible = false; return hud.save(); }))
                .then(literal("show").executes(context -> { hud.visible = true; return hud.save(); }))
                .then(literal("size").then(argument("width", IntegerArgumentType.integer(24, 256))
                    .executes(context -> { hud.width = IntegerArgumentType.getInteger(context, "width"); return hud.save(); })))
                .then(literal("offset").then(argument("right", IntegerArgumentType.integer(0, 2048))
                    .then(argument("bottom", IntegerArgumentType.integer(0, 2048))
                        .executes(context -> {
                            hud.right = IntegerArgumentType.getInteger(context, "right");
                            hud.bottom = IntegerArgumentType.getInteger(context, "bottom");
                            return hud.save();
                        }))))
                .then(literal("reset").executes(context -> {
                    hud.visible = true; hud.width = 96; hud.right = 12; hud.bottom = 36;
                    return hud.save();
                }))));
    }

    private void render(DrawContext context, RenderTickCounter tickCounter) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (!visible || client.world == null || client.player == null || client.options.hudHidden
                || client.currentScreen != null) {
            return;
        }
        CharacterPlacement.Bounds bounds = CharacterPlacement.fit(
            context.getScaledWindowWidth(), context.getScaledWindowHeight(), width, right, bottom);
        // Draw the user's original PNG intact, including alpha and its original aspect ratio.
        context.drawTexture(RenderPipelines.GUI_TEXTURED, TEXTURE, bounds.x(), bounds.y(),
            0, 0, bounds.width(), bounds.height(), 728, 680, 728, 680);
    }

    private void load() {
        if (!Files.exists(configPath)) {
            save();
            return;
        }
        Properties properties = new Properties();
        try (InputStream input = Files.newInputStream(configPath)) {
            properties.load(input);
            visible = Boolean.parseBoolean(properties.getProperty("visible", "true"));
            width = readInt(properties, "width", 96, 24, 256);
            right = readInt(properties, "right", 12, 0, 2048);
            bottom = readInt(properties, "bottom", 36, 0, 2048);
        } catch (IOException | IllegalArgumentException error) {
            LOGGER.warn("Could not load character placement; using defaults", error);
        }
    }

    private static int readInt(Properties properties, String key, int fallback, int min, int max) {
        try {
            return Math.clamp(Integer.parseInt(properties.getProperty(key, "").trim()), min, max);
        } catch (NumberFormatException error) {
            return fallback;
        }
    }

    private int save() {
        Properties properties = new Properties();
        properties.setProperty("visible", Boolean.toString(visible));
        properties.setProperty("width", Integer.toString(width));
        properties.setProperty("right", Integer.toString(right));
        properties.setProperty("bottom", Integer.toString(bottom));
        try {
            Files.createDirectories(configPath.getParent());
            try (OutputStream output = Files.newOutputStream(configPath)) {
                properties.store(output, "Dogido character placement (GUI scaled pixels)");
            }
            return 1;
        } catch (IOException error) {
            LOGGER.warn("Character placement changed for this session but could not be saved", error);
            MinecraftClient client = MinecraftClient.getInstance();
            if (client.player != null) {
                client.player.sendMessage(Text.literal("ドギドの位置は変更しましたが、設定の保存に失敗しました。"), false);
            }
            return 0;
        }
    }
}
