from django.apps import AppConfig


class CommsConfig(AppConfig):
    name = 'comms'

    def ready(self):
        import comms.signals

