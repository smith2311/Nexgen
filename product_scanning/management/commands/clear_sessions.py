from django.core.management.base import BaseCommand, CommandError

from product_scanning.models import ScannedProduct, ScanningSession


class Command(BaseCommand):
    help = "Clear all scanning sessions and scanned products for fresh testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-completed",
            action="store_true",
            dest="include_completed",
            default=False,
            help="Also delete completed sessions (by default only active sessions are cleared)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            dest="force",
            default=False,
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        include_completed = options["include_completed"]
        force = options["force"]

        # Get counts before deletion
        if include_completed:
            sessions_to_delete = ScanningSession.objects.all()
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  WARNING: You are about to delete ALL scanning sessions and products (both active and completed)!"
                )
            )
        else:
            sessions_to_delete = ScanningSession.objects.filter(status="active")
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  WARNING: You are about to delete ALL ACTIVE scanning sessions and their products!"
                )
            )

        session_count = sessions_to_delete.count()
        product_count = ScannedProduct.objects.filter(scanning_session__in=sessions_to_delete).count()

        if session_count == 0:
            self.stdout.write(self.style.SUCCESS("✅ No sessions to delete"))
            return

        self.stdout.write(f"\n📊 Sessions to delete: {session_count}")
        self.stdout.write(f"📊 Scanned products to delete: {product_count}")

        if not force:
            confirmation = input("\nDo you want to continue? (yes/no): ")
            if confirmation.lower() != "yes":
                self.stdout.write(self.style.WARNING("❌ Cancelled"))
                return

        try:
            # Delete scanned products first (to respect foreign keys)
            ScannedProduct.objects.filter(scanning_session__in=sessions_to_delete).delete()

            # Delete sessions
            deleted_count, _ = sessions_to_delete.delete()

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✅ Successfully deleted {session_count} session(s) and {product_count} product(s)!"
                )
            )

        except Exception as e:
            raise CommandError(f"Error deleting sessions: {str(e)}")
