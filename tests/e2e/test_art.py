"""The browser's art loader: a sheet and its separate alpha are combined, a color map recolors it, and the result is cached.

The pictures are drawn in the page and handed to the loader in place of downloads, so no asset file is needed.
"""

from playwright.sync_api import expect


def test_a_sheet_is_combined_with_its_alpha_and_recolored_by_a_color_map(page):
    page.goto("/")
    expect(page.locator("html")).to_have_attribute("data-example-ready", "true")
    result = page.evaluate(
        """async () => {
            const { Art, nearest, parseHex } = await import("/client/js/art.js");
            const picture = (pixels) => {  // a 2x1 canvas
                const canvas = Object.assign(document.createElement("canvas"), { width: 2, height: 1 });
                const context = canvas.getContext("2d");
                context.putImageData(new ImageData(new Uint8ClampedArray(pixels), 2, 1), 0, 0);
                return canvas;
            };
            const files = {
                "knight.png": picture([48, 80, 160, 255, 49, 81, 161, 255]),  // the second is only nearly the first color
                "knight.alpha.png": picture([255, 255, 255, 255, 128, 128, 128, 255]),
            };
            const fetched = [];
            const art = new Art((name) => name, {
                fetchJson: async (url) => {
                    fetched.push(url);
                    return url === "knight.sheet.json"
                        ? { frame: [1, 1], grid: [2, 1], frames: 2, palette: ["#3050a0", "#ffffff"] }
                        : { "#3050a0": "#a03030" };
                },
                loadImage: async (url) => files[url],
            });
            const plain = await art.load({ sheet: "knight" });
            const red = await art.load({ sheet: "knight", colors: "red_team" });
            const again = await art.load({ sheet: "knight", colors: "red_team" });
            const read = (loaded) => Array.from(loaded.source.getContext("2d").getImageData(0, 0, 2, 1).data);
            return {
                plain: read(plain), red: read(red), cached: red === again, frames: red.frames,
                nearest: nearest(parseHex("#3151a1"), [parseHex("#ffffff"), parseHex("#3050a0")]),
                fetched,
            };
        }"""
    )

    def close(got, want):  # a canvas keeps half-see-through pixels premultiplied, so their colors come back a little off
        return all(abs(a - b) <= 2 for a, b in zip(got, want))

    assert result["plain"][:4] == [48, 80, 160, 255] and result["plain"][7] == 128, "the alpha sheet's gray becomes the alpha"
    assert result["red"][:4] == [160, 48, 48, 255], "the color map swaps the color"
    assert close(result["red"][4:7], [160, 48, 48]) and result["red"][7] == 128, "a nearly-matching color is snapped to the palette first, then swapped"
    assert result["cached"] and result["frames"] == 2 and result["nearest"] == 1
    assert result["fetched"].count("red_team.colors.json") == 1


def test_a_plain_file_name_still_loads_as_one_image(page):
    page.goto("/")
    expect(page.locator("html")).to_have_attribute("data-example-ready", "true")
    result = page.evaluate(
        """async () => {
            const { Art } = await import("/client/js/art.js");
            const art = new Art((name) => `/assets/${name}`);
            const loaded = await art.load("grass.svg");
            return { width: loaded.source.naturalWidth, frame: loaded.frame, palette: loaded.palette };
        }"""
    )
    assert result["width"] > 0 and result["frame"] is None and result["palette"] is None


def test_the_example_games_crystal_sheet_loads_and_its_color_map_turns_it_red(page):
    page.goto("/")
    expect(page.locator("html")).to_have_attribute("data-example-ready", "true")
    result = page.evaluate(
        """async () => {
            const { Art, frameRect } = await import("/client/js/art.js");
            const art = new Art((name) => `/assets/${name}`);
            const blue = await art.load({ sheet: "crystal" });
            const red = await art.load({ sheet: "crystal", colors: "crystal_red" });
            const pixel = (loaded, x, y) => Array.from(loaded.source.getContext("2d").getImageData(x, y, 1, 1).data);
            return {
                blue: pixel(blue, 8, 12), red: pixel(red, 8, 12), corner: pixel(red, 0, 0),
                size: [red.source.width, red.source.height], frames: red.frames, second: frameRect(red, 1),
            };
        }"""
    )
    assert result["blue"] == [48, 80, 160, 255] and result["red"] == [160, 48, 48, 255]
    assert result["corner"][3] == 0, "outside the diamond the alpha sheet makes it transparent"
    assert result["size"] == [32, 16] and result["frames"] == 2
    assert result["second"] == {"x": 16, "y": 0, "w": 16, "h": 16}
