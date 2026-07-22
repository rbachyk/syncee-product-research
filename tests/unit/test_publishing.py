"""Unit tests for the publish-prep phase (normalize, SEO, images, service)."""

import io
import json

from PIL import Image

from syncee_scanner.config import load_config
from syncee_scanner.models import PublishPrepStatus, SelectionStatus
from syncee_scanner.publishing import images, normalize, seo, service
from syncee_scanner.runs.persistence import InMemoryPersistence


def cfg():
    return load_config()


def _png(w: int, h: int, color=(200, 160, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FakeTransport:
    """Offline stand-in for OpenRouter: canned SEO JSON + a generated PNG."""

    def __init__(self):
        self.chat_calls = 0
        self.image_calls = 0

    def chat(self, model, messages, *, temperature=0.4, max_tokens=1200, json_mode=False):
        self.chat_calls += 1
        return json.dumps({
            "title": "Olive Wood Serving Board",
            "seo_title": "Olive Wood Serving Board | Handcrafted Kitchen Board",
            "meta_description": "A handcrafted olive-wood board for everyday serving.",
            "description_html": "<p>Beautiful and durable.</p><ul><li>Olive wood</li></ul>",
            "image_alt_text": "Olive wood serving board on a white background",
            "tags": ["kitchen", "serving board", "olive wood", "kitchen"],
        })

    def edit_image(self, model, prompt, image_bytes, *, mime="image/png"):
        self.image_calls += 1
        return _png(1024, 1024, (255, 255, 255))


# --- normalize -------------------------------------------------------------------

class TestNormalize:
    def test_material_multilingual(self):
        assert normalize.parse_material("Tagliere in legno d'ulivo") == "Olive wood"
        assert normalize.parse_material("Edelstahl Küchenhelfer") == "Stainless steel"
        assert normalize.parse_material("a plain thing") is None

    def test_dimensions_and_weight(self):
        assert normalize.parse_dimensions("Bowl Ø15x6cm ceramic") is not None
        assert normalize.parse_weight("scented candle 450g net") == "450g"

    def test_product_type_by_collection(self):
        got = normalize.product_type_for({"Collection": "Kitchen Convenience"})
        assert got == "Kitchen & Dining"

    def test_handle_slug(self):
        got = normalize.handle_from("Olive Wood Serving Board!", "pid:x")
        assert got == "olive-wood-serving-board"

    def test_placeholder_category_not_tagged(self):
        tags = normalize.base_tags(
            {"Collection": "Kitchen Convenience", "Syncee Category": "n/a"}, None
        )
        assert "n/a" not in [t.lower() for t in tags]
        assert "Kitchen Convenience" in tags

    def test_normalize_fields(self):
        f = normalize.normalize_fields({
            "Collection": "Kitchen Convenience", "Product Name": "Tabla de oliva",
            "Description": "madera 30x20 cm", "Main Image URL": "http://x/a.jpg",
        })
        assert f["Vendor"] == "RB Home"
        assert f["Material"] in ("Olive wood", "Wood")
        assert "Publish Tags" not in f  # tags are content-owned (set by the SEO step)
        assert f["Original Image URL"] == "http://x/a.jpg"


# --- seo -------------------------------------------------------------------------

class TestSeo:
    def test_prompt_contains_facts_and_is_grounded(self):
        msgs = seo.build_messages(
            {"Product Name": "Tagliere", "Collection": "Kitchen Convenience"},
            {"Product Type": "Kitchen & Dining", "Material": "Olive wood"}, cfg(),
        )
        assert msgs[0]["role"] == "system" and "never invent" in msgs[0]["content"].lower()
        assert "Tagliere" in msgs[1]["content"]

    def test_generate_seo_fields_and_clipping(self):
        c = cfg()
        out = seo.generate_seo(
            {"Product Name": "x", "Product Key": "pid:1", "Collection": "Kitchen Convenience"},
            {"Material": "Olive wood"}, FakeTransport(), c,
        )
        assert out["Cleaned Title"] == "Olive Wood Serving Board"
        assert len(out["SEO Title"]) <= c.publishing.seo.max_title_len
        assert len(out["Meta Description"]) <= c.publishing.seo.max_meta_len
        assert out["Handle"] == "olive-wood-serving-board"
        # tags = base (Collection/Material/RB Home) + English model tags, de-duplicated
        tags = [t.strip().lower() for t in out["Publish Tags"].split(",")]
        assert tags.count("kitchen") == 1 and "kitchen convenience" in tags
        assert "rb home" in tags and "olive wood" in tags

    def test_retries_then_succeeds(self):
        class Flaky(FakeTransport):
            def __init__(self):
                super().__init__()
                self.n = 0

            def chat(self, *a, **k):
                self.n += 1
                return "garbage" if self.n == 1 else FakeTransport.chat(self, *a, **k)
        ft = Flaky()
        out = seo.generate_seo({"Product Name": "x", "Product Key": "pid:1"}, {}, ft, cfg())
        assert out["Cleaned Title"] == "Olive Wood Serving Board" and ft.n == 2

    def test_invalid_json_raises(self):
        class Bad(FakeTransport):
            def chat(self, *a, **k):
                return "not json at all"
        import pytest

        from syncee_scanner.observability.errors import ScannerError
        with pytest.raises(ScannerError):
            seo.generate_seo({"Product Name": "x"}, {}, Bad(), cfg())


# --- images ----------------------------------------------------------------------

class TestImages:
    def test_finish_is_square_target_jpeg(self):
        out = images.finish_image(_png(400, 300), cfg())
        with Image.open(io.BytesIO(out)) as im:
            assert im.size == (2048, 2048)
            assert im.format == "JPEG"

    def test_padding_samples_background_not_white(self):
        # Portrait grey image → square padding must match the grey background, no white bands.
        grey = (234, 233, 231)
        im = Image.new("RGB", (400, 600), grey)
        im.paste((200, 20, 20), (100, 200, 300, 400))  # product-ish block in the centre
        buf = io.BytesIO()
        im.save(buf, "PNG")
        out = images.finish_image(buf.getvalue(), cfg())
        with Image.open(io.BytesIO(out)) as res:
            corner = res.getpixel((5, res.height // 2))  # padded side column
        assert max(abs(corner[i] - grey[i]) for i in range(3)) <= 6  # ~grey, not (255,255,255)

    def test_assess_source_flags_low_res(self):
        assert images.assess_source(_png(500, 500), cfg())["low_res"] is True
        assert images.assess_source(_png(1200, 1200), cfg())["low_res"] is False

    def test_transform_prompt_keeps_product_strips_rest(self):
        p = images.build_transform_prompt(cfg()).lower()
        assert "keep the product exactly as it is" in p
        assert "remove everything that is not the product" in p
        assert "promotional text" in p and "square" in p  # strips branding, square framing


# --- service ---------------------------------------------------------------------

class TestService:
    def _seed(self):
        p = InMemoryPersistence()
        p.products["pid:1"] = {
            "id": 1, "Product Key": "pid:1", "Product Name": "Tagliere in legno d'ulivo",
            "Description": "Tabla 30x20 cm", "Collection": "Kitchen Convenience",
            "Selection Status": SelectionStatus.INITIAL_ASSORTMENT_CANDIDATE.value,
            "Main Image URL": None,  # skip network image; content-only path
        }
        return p

    def test_prep_content_only_sets_status(self):
        p = self._seed()
        [summary] = service.run_publish_prep(p, cfg(), FakeTransport(), do_images=False)
        row = p.products["pid:1"]
        assert summary["content"] is True
        assert row["Cleaned Title"] == "Olive Wood Serving Board"
        assert row["Vendor"] == "RB Home"
        assert row["Publish-Prep Status"] == PublishPrepStatus.CONTENT_READY.value

    def test_only_eligible_products_targeted(self):
        p = self._seed()
        p.products["pid:2"] = {"id": 2, "Product Key": "pid:2", "Product Name": "y",
                               "Selection Status": SelectionStatus.NOT_SELECTED.value}
        targets = service.iter_targets(p)
        assert [t["Product Key"] for t in targets] == ["pid:1"]

    def test_image_path_uploads_and_flags(self):
        p = self._seed()
        p.products["pid:1"]["Main Image URL"] = "http://x/a.jpg"
        c = cfg()
        c.publishing.images.method = "generative"  # force generative (no rembg in unit tests)
        ft = FakeTransport()
        images_orig = images.download
        images.download = lambda url, **k: _png(500, 500)  # low-res source
        try:
            [summary] = service.run_publish_prep(p, c, ft, keys=["pid:1"])
        finally:
            images.download = images_orig
        assert summary["image"] is True and ft.image_calls == 1
        assert p.products["pid:1"]["Processed Image"]  # attachment set
        # low-res source → flagged → Needs Attention
        assert p.products["pid:1"]["Publish-Prep Status"] == PublishPrepStatus.NEEDS_ATTENTION.value


# --- hybrid routing + cutout -----------------------------------------------------

class TestCutout:
    def _noisy_png(self, w, h):
        import random
        im = Image.new("RGB", (w, h))
        px = im.load()
        rnd = random.Random(0)
        for y in range(h):
            for x in range(w):
                px[x, y] = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    def test_classify_clean_vs_lifestyle(self):
        assert images.classify_shot(_png(1000, 1000, (240, 240, 240)), cfg()) == "clean"
        assert images.classify_shot(self._noisy_png(300, 300), cfg()) == "lifestyle"

    def test_route_honours_method(self):
        c = cfg()
        c.publishing.images.method = "generative"
        assert images._route(_png(800, 800, (240, 240, 240)), c) == "generative"
        c.publishing.images.method = "cutout"
        assert images._route(self._noisy_png(200, 200), c) == "cutout"

    def test_composite_on_white_centers_product(self):
        from syncee_scanner.publishing import cutout
        # A red square product on transparent, off-centre in the source.
        rgba = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
        rgba.paste((200, 30, 30, 255), (50, 50, 250, 300))
        out = cutout.composite_on_white(rgba, cfg())
        with Image.open(io.BytesIO(out)) as res:
            assert res.size == (2048, 2048) and res.format == "JPEG"
            corner = res.getpixel((5, 5))
            assert corner != (200, 30, 30)      # tinted backdrop, not the product
            assert min(corner) > 150            # light studio tone
            assert res.getpixel((1024, 1024)) != corner  # product sits at the centre

    def test_backdrop_picks_calm_pastel(self):
        from syncee_scanner.publishing import cutout
        cut = cfg().publishing.images.cutout
        palette = [cutout._hex(p) for p in cut.pastel_palette]
        # colourful product → a pastel from the palette (never a grey average)
        red = Image.new("RGBA", (64, 64), (220, 40, 40, 255))
        assert cutout._backdrop_tone(red, cut) in palette
        # neutral product (wood/grey) → still a light pastel, not dark/grey
        neutral = Image.new("RGBA", (64, 64), (150, 148, 145, 255))
        tone = cutout._backdrop_tone(neutral, cut)
        assert tone in palette and min(tone) > 180

    def test_cutout_on_white_uses_injected_remover(self):
        from syncee_scanner.publishing import cutout

        def fake_remover(_bytes):
            rgba = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
            rgba.paste((10, 120, 200, 255), (60, 60, 240, 240))
            return rgba
        out = cutout.cutout_on_white(b"ignored", cfg(), remover=fake_remover)
        with Image.open(io.BytesIO(out)) as res:
            assert res.size == (2048, 2048)
            assert res.getpixel((1024, 1024)) != (255, 255, 255)  # product present
