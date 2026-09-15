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

/** Permanent companion, independent of workshop and server availability. */
final class DogidoCharacterHud {
    private static final Logger LOGGER = LoggerFactory.getLogger("dogido-character-hud");
    private static final Identifier TEXTURE = Identifier.of("dogido", "textures/gui/character.png");
    private static final Identifier CLOSED_TEXTURE = Identifier.of("dogido", "textures/gui/character_closed.png");
    private final long animationStartedNanos = System.nanoTime();
    private final Path configPath = FabricLoader.getInstance().getConfigDir().resolve("dogido-character.properties");
    private boolean visible = true;
    private boolean autoLayout = true;
    private boolean motion = true;
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
                        "ドギド表示: 左下・右向き。/dogidohud size 96（GUI幅）・offset 12 36（左・下）・hide / show・motion on|off・reset"));
                    return 1;
                })
                .then(literal("hide").executes(context -> { hud.visible = false; return hud.save(); }))
                .then(literal("show").executes(context -> { hud.visible = true; return hud.save(); }))
                .then(literal("size").then(argument("width", IntegerArgumentType.integer(24, 256))
                    .executes(context -> { hud.autoLayout = false; hud.width = IntegerArgumentType.getInteger(context, "width"); return hud.save(); })))
                .then(literal("offset").then(argument("right", IntegerArgumentType.integer(0, 2048))
                    .then(argument("bottom", IntegerArgumentType.integer(0, 2048))
                        .executes(context -> {
                            hud.autoLayout = false;
                            hud.right = IntegerArgumentType.getInteger(context, "right");
                            hud.bottom = IntegerArgumentType.getInteger(context, "bottom");
                            return hud.save();
                        }))))
                .then(literal("reset").executes(context -> {
                    hud.visible = true; hud.autoLayout = true; hud.motion = true;
                    hud.width = 96; hud.right = 12; hud.bottom = 36;
                    return hud.save();
                }))
                .then(literal("motion")
                    .then(literal("off").executes(context -> { hud.motion = false; return hud.save(); }))
                    .then(literal("on").executes(context -> { hud.motion = true; return hud.save(); })))));
    }

    private void render(DrawContext context, RenderTickCounter tickCounter) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (!visible || client.world == null || client.player == null || client.options.hudHidden
                || client.currentScreen != null) {
            return;
        }
        CharacterPlacement.Bounds bounds = autoLayout
            ? CharacterPlacement.approved(context.getScaledWindowWidth(), context.getScaledWindowHeight())
            : CharacterPlacement.lowerLeft(context.getScaledWindowWidth(), context.getScaledWindowHeight(), width, right, bottom);
        float floatOffset = motion ? (float) ((1 - Math.cos(System.nanoTime() / 1_000_000_000.0 * Math.PI * 2 / 3.8))
            * context.getScaledWindowWidth() * -.0025) : 0;
        // Use the author's right-facing artwork as-is: never mirror geometry or UVs.
        context.getMatrices().pushMatrix();
        context.getMatrices().translate(0, floatOffset);
        Identifier texture = CharacterBlink.closed((System.nanoTime() - animationStartedNanos) / 1_000_000, motion)
            ? CLOSED_TEXTURE : TEXTURE;
        context.drawTexture(RenderPipelines.GUI_TEXTURED, texture, bounds.x(), bounds.y(),
            0, 0, bounds.width(), bounds.height(), CharacterPlacement.TEXTURE_WIDTH,
            CharacterPlacement.TEXTURE_HEIGHT, CharacterPlacement.TEXTURE_WIDTH, CharacterPlacement.TEXTURE_HEIGHT);
        context.getMatrices().popMatrix();
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
            autoLayout = Boolean.parseBoolean(properties.getProperty("auto_layout", "true"));
            motion = Boolean.parseBoolean(properties.getProperty("motion", "true"));
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
        properties.setProperty("auto_layout", Boolean.toString(autoLayout));
        properties.setProperty("motion", Boolean.toString(motion));
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
