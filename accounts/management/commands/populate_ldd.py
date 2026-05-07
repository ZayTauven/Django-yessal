from django.core.management.base import BaseCommand
from accounts.models import Daara, LDD


class Command(BaseCommand):
    help = "Populate default LDD and assign to all existing Daaras"

    def handle(self, *args, **options):
        # créer un LDD par défaut
        ldd_default, created = LDD.objects.get_or_create(
            code="DEFAULT",
            defaults={"name": "LDD par défaut"}
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS(f"✓ Created LDD: {ldd_default.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠ LDD already exists: {ldd_default.name}"))

        # assigner aux daaras existants
        updated_count = 0
        for daara in Daara.objects.all():
            if daara.ldd != ldd_default:
                daara.ldd = ldd_default
                daara.save()
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"✓ Updated {updated_count} Daara(s) with LDD: {ldd_default.name}")
        )
