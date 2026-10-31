from django.apps import AppConfig


class ContributionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contributions'

    def ready(self):
        import contributions.signals  # noqa: F401 — branche les receveurs
