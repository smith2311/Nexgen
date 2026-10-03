"""Generate the PNG icons the web app manifest needs, from the Meril logo.

NexGen shipped with one brand image usable as an icon — a 148x148 JPEG — which
is too small for the manifest (Chrome wants 192 and 512) and the wrong format
for a maskable icon. Rather than commit four hand-made PNGs whose provenance
nobody remembers, this rebuilds all of them from the 800x800 source whenever
the branding changes.

The output IS committed. The deploy runs collectstatic, not an image build, so
the files have to be on disk in the repo — this command is how they are
refreshed, not a build step.

What "maskable" means and why it is a separate file: Android crops an icon to
whatever shape the launcher uses — circle, squircle, rounded square — and only
the middle 80% is guaranteed to survive. A maskable icon therefore has to carry
its own background and keep the logo well inside that safe zone, which is a
different image from the one used where no cropping happens. Declaring one file
as both `any` and `maskable` gets you a logo cropped to a circle on some
launchers and floating in dead space on others.

Usage (from inside the running container):
    python manage.py make_pwa_icons
    python manage.py make_pwa_icons --source "static/img/Meril Logo.jpg"
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

#: The Meril blue the navbar starts on — static/css/modern-ui.css:7. The
#: maskable icon and the iOS icon both need an opaque ground (iOS applies its
#: own rounding and renders transparency as black), and this is the one that
#: matches the app it opens.
BRAND = (0x00, 0x76, 0xC8)

#: Fraction of the canvas the logo occupies on a maskable icon. Android's safe
#: zone is the middle 80%; 60% leaves room for the crop to bite without ever
#: reaching the mark.
MASKABLE_LOGO_SCALE = 0.60

#: Same idea for iOS, which crops far less aggressively — it only rounds the
#: corners — so the logo can sit larger.
APPLE_LOGO_SCALE = 0.80

DEFAULT_SOURCE = "img/Meril Logo.jpg"
OUTPUT_DIR = "img/pwa"


class Command(BaseCommand):
    help = "Regenerate the PWA icons in static/img/pwa/ from the Meril logo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=None,
            help=f"Path to the source logo, relative to the repo root (default: static/{DEFAULT_SOURCE}).",
        )

    def handle(self, *args, **options):
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow is a hard dep of the deps
            raise CommandError("Pillow is required to generate the icons.") from exc

        static_dir = Path(settings.STATICFILES_DIRS[0])
        source_path = (
            Path(options["source"]) if options["source"] else static_dir / DEFAULT_SOURCE
        )
        if not source_path.exists():
            raise CommandError(f"Source logo not found: {source_path}")

        out_dir = static_dir / OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        logo = Image.open(source_path).convert("RGB")
        self.stdout.write(f"Source: {source_path} ({logo.width}x{logo.height})")
        if min(logo.size) < 512:
            # Upscaling a small source produces a soft icon, which is the one
            # part of an installed app a user looks at every day.
            self.stdout.write(
                self.style.WARNING(
                    "  source is smaller than 512px — the 512 icon will be upscaled and soft"
                )
            )

        written = []
        # Plain icons: the logo fills the canvas, because nothing crops these.
        for size in (192, 512):
            icon = logo.resize((size, size), Image.LANCZOS)
            path = out_dir / f"icon-{size}.png"
            icon.save(path, "PNG", optimize=True)
            written.append(path)

        written.append(self._on_ground(Image, logo, 512, MASKABLE_LOGO_SCALE,
                                       out_dir / "icon-512-maskable.png"))
        written.append(self._on_ground(Image, logo, 180, APPLE_LOGO_SCALE,
                                       out_dir / "apple-touch-icon.png"))

        for path in written:
            self.stdout.write(self.style.SUCCESS(f"  wrote {path.relative_to(static_dir.parent)}"))
        self.stdout.write(
            "Commit these — collectstatic copies them, nothing regenerates them on deploy."
        )

    def _on_ground(self, Image, logo, size, scale, path):
        """The logo centred at `scale` on an opaque brand-coloured square."""
        canvas = Image.new("RGB", (size, size), BRAND)
        inner = max(1, int(size * scale))
        canvas.paste(logo.resize((inner, inner), Image.LANCZOS),
                     ((size - inner) // 2, (size - inner) // 2))
        canvas.save(path, "PNG", optimize=True)
        return path
