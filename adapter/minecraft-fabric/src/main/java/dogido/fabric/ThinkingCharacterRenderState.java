package dogido.fabric;

import com.mojang.blaze3d.pipeline.RenderPipeline;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gl.RenderPipelines;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.ScreenRect;
import net.minecraft.client.gui.render.state.SimpleGuiElementRenderState;
import net.minecraft.client.render.VertexConsumer;
import net.minecraft.client.texture.TextureSetup;
import net.minecraft.util.Identifier;
import org.joml.Matrix3x2f;

/** One connected textured mesh; rigid inner cells avoid overlay seams/doubled ink. */
record ThinkingCharacterRenderState(TextureSetup textureSetup, Matrix3x2f pose,
        CharacterPlacement.Bounds placement, ThinkingMesh.Point[] points,
        ScreenRect scissorArea, ScreenRect bounds) implements SimpleGuiElementRenderState {
    static void draw(DrawContext context, Identifier textureId, CharacterPlacement.Bounds placement, long now) {
        var texture = MinecraftClient.getInstance().getTextureManager().getTexture(textureId);
        var pose = new Matrix3x2f(context.getMatrices());
        int pad = (int) Math.ceil(placement.width() * ThinkingMesh.MAX_OFFSET / ThinkingMesh.WIDTH) + 2;
        var bounds = new ScreenRect(placement.x() - pad, placement.y() - pad,
            placement.width() + pad * 2, placement.height() + pad * 2).transformEachVertex(pose);
        var scissor = context.scissorStack.peekLast();
        if (scissor != null) bounds = bounds.intersection(scissor);
        if (bounds == null) return;
        var points = new ThinkingMesh.Point[(ThinkingMesh.COLUMNS + 1) * (ThinkingMesh.ROWS + 1)];
        for (int row = 0; row <= ThinkingMesh.ROWS; row++) for (int col = 0; col <= ThinkingMesh.COLUMNS; col++)
            points[row * (ThinkingMesh.COLUMNS + 1) + col] = ThinkingMesh.point(col, row, now, true);
        context.state.addSimpleElement(new ThinkingCharacterRenderState(
            TextureSetup.of(texture.getGlTextureView(), texture.getSampler()), pose, placement, points, scissor, bounds));
    }

    @Override public RenderPipeline pipeline() { return RenderPipelines.GUI_TEXTURED; }
    @Override public void setupVertices(VertexConsumer vertices) {
        for (int row = 0; row < ThinkingMesh.ROWS; row++) for (int col = 0; col < ThinkingMesh.COLUMNS; col++) {
            // Match vanilla GUI quad winding; never mirror UVs or apply negative scale.
            vertex(vertices, col, row);
            vertex(vertices, col, row + 1);
            vertex(vertices, col + 1, row + 1);
            vertex(vertices, col + 1, row);
        }
    }
    private void vertex(VertexConsumer vertices, int column, int row) {
        var p = points[row * (ThinkingMesh.COLUMNS + 1) + column];
        vertices.vertex(pose, placement.x() + p.x() * placement.width() / ThinkingMesh.WIDTH,
            placement.y() + p.y() * placement.height() / ThinkingMesh.HEIGHT).texture(p.u(), p.v()).color(-1);
    }
}
