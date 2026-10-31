package dogido.fabric.screenshot;

import java.awt.image.BufferedImage;

/** Pillow 12.2 RGB/BOXと同じ画素中心・22bit係数・各軸8bit丸めで原画像を縮小する。
 * アルゴリズム参照: python-pillow/Pillow 12.2.0 libImaging/Resample.c, PIL/Image.py。
 * 画像注釈や表示用192px画像には触らない。
 */
final class BodyViewBox {
    private record Weights(int first, int[] coefficients) {}
    private BodyViewBox() {}

    static int[] samples(BufferedImage image) {
        int width = image.getWidth(), height = image.getHeight();
        int[] rgb = new int[width * height * 3];
        for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) {
            int pixel = image.getRGB(x, y), i = (y * width + x) * 3;
            rgb[i] = (pixel >> 16) & 255; rgb[i + 1] = (pixel >> 8) & 255; rgb[i + 2] = pixel & 255;
        }
        // Pillowは極端に縦長の画像を先に縦縮小する。通常の画面は横→縦。
        if ((long)height > (long)width * 100 && height > 48) {
            return horizontal(vertical(rgb, width, height, 48), width, 48, 64);
        }
        return vertical(horizontal(rgb, width, height, 64), 64, height, 48);
    }

    private static Weights[] weights(int source, int target) {
        Weights[] result = new Weights[target];
        double scale = (double)source / target, filterScale = Math.max(1.0, scale);
        for (int i = 0; i < target; i++) {
            double center = (i + .5) * scale, support = .5 * filterScale;
            int first = Math.max(0, (int)(center - support + .5));
            int end = Math.min(source, (int)(center + support + .5));
            int[] coefficients = new int[end - first];
            int count = 0;
            for (int j = first; j < end; j++) {
                double offset = (j - center + .5) * (1.0 / filterScale);
                if (offset > -.5 && offset <= .5) { coefficients[j - first] = 1; count++; }
            }
            if (count == 0) throw new IllegalArgumentException("empty_box_support");
            int coefficient = (int)(.5 + (1.0 / count) * (1 << 22));
            for (int j = 0; j < coefficients.length; j++) coefficients[j] *= coefficient;
            result[i] = new Weights(first, coefficients);
        }
        return result;
    }
    private static int[] horizontal(int[] rgb, int width, int height, int target) {
        if (width == target) return rgb;
        Weights[] weights = weights(width, target);
        int[] result = new int[target * height * 3];
        for (int y = 0; y < height; y++) for (int x = 0; x < target; x++) for (int c = 0; c < 3; c++) {
            Weights w = weights[x];
            long value = 1 << 21;
            for (int j = 0; j < w.coefficients.length; j++) value += (long)rgb[(y * width + w.first + j) * 3 + c] * w.coefficients[j];
            result[(y * target + x) * 3 + c] = (int)Math.max(0, Math.min(255, value >> 22));
        }
        return result;
    }
    private static int[] vertical(int[] rgb, int width, int height, int target) {
        if (height == target) return rgb;
        Weights[] weights = weights(height, target);
        int[] result = new int[width * target * 3];
        for (int y = 0; y < target; y++) for (int x = 0; x < width; x++) for (int c = 0; c < 3; c++) {
            Weights w = weights[y];
            long value = 1 << 21;
            for (int j = 0; j < w.coefficients.length; j++) value += (long)rgb[((w.first + j) * width + x) * 3 + c] * w.coefficients[j];
            result[(y * width + x) * 3 + c] = (int)Math.max(0, Math.min(255, value >> 22));
        }
        return result;
    }
}
