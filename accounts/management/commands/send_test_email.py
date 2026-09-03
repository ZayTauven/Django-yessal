"""Vérifie la configuration SMTP en envoyant un vrai courriel.

À lancer AVANT de brancher le moindre déclencheur métier : si la connexion ne
passe pas, autant le savoir sur une commande qu'on relance en deux secondes
plutôt qu'en débogant une inscription qui n'envoie rien.

    python manage.py send_test_email vous@exemple.com
    python manage.py send_test_email vous@exemple.com --code mot_de_passe_oublie

Sans `--code`, un message de diagnostic est composé à la volée : il n'exige
aucun gabarit et récapitule la configuration effective, ce qui permet de
distinguer « SMTP muet » de « gabarit manquant ».
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.management.base import BaseCommand, CommandError

from core.mail import SUJETS


class Command(BaseCommand):
    help = "Envoie un courriel de test pour valider la configuration SMTP."

    def add_arguments(self, parser):
        parser.add_argument('destinataire', help="Adresse à laquelle envoyer le test.")
        parser.add_argument(
            '--code',
            help=(
                "Envoie un vrai gabarit au lieu du message de diagnostic. "
                f"Codes connus : {', '.join(sorted(SUJETS))}."
            ),
        )

    def handle(self, *args, **options):
        destinataire = options['destinataire']
        code = options.get('code')

        # ── Ce que Django s'apprête réellement à faire ─────────────────────
        # Affiché AVANT l'envoi : quand rien n'arrive, la première question est
        # toujours « quel backend, quel hôte, quel expéditeur ».
        self.stdout.write("Configuration effective :")
        self.stdout.write(f"  EMAIL_BACKEND      {settings.EMAIL_BACKEND}")
        self.stdout.write(f"  EMAIL_HOST         {settings.EMAIL_HOST or '(vide)'}")
        self.stdout.write(f"  EMAIL_PORT         {settings.EMAIL_PORT}")
        self.stdout.write(f"  EMAIL_HOST_USER    {settings.EMAIL_HOST_USER or '(vide)'}")
        self.stdout.write(
            f"  EMAIL_HOST_PASSWORD {'renseigné' if settings.EMAIL_HOST_PASSWORD else '(VIDE)'}"
        )
        self.stdout.write(f"  EMAIL_USE_TLS      {settings.EMAIL_USE_TLS}")
        self.stdout.write(f"  DEFAULT_FROM_EMAIL {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"  EMAIL_ENABLED      {settings.EMAIL_ENABLED}")
        self.stdout.write("")

        if not settings.EMAIL_HOST:
            self.stdout.write(self.style.WARNING(
                "EMAIL_HOST est vide : Django écrit sur la sortie standard au lieu "
                "d'envoyer. Le message ci-dessous ne partira nulle part."
            ))
        if not settings.EMAIL_ENABLED:
            raise CommandError(
                "EMAIL_ENABLED=False : les envois sont coupés. "
                "Passez-le à True pour tester."
            )

        if code:
            if code not in SUJETS:
                raise CommandError(
                    f"Code inconnu : {code!r}. Codes connus : {', '.join(sorted(SUJETS))}"
                )
            from core.mail import _contexte_commun, _message
            from core.mail_samples import CONTEXTES

            # Le même jeu de valeurs que celui des tests : un aperçu qui ne
            # montrerait pas les coordonnées bancaires ou le montant ne dirait
            # rien de la mise en page réelle. Voir core/mail_samples.py.
            exemple = dict(CONTEXTES.get(code, {}))
            exemple.setdefault(
                'lien_reinitialisation',
                f'{settings.BASE_URL}/reset-password?token=EXEMPLE',
            )
            message = _message(destinataire, code, _contexte_commun(exemple))
        else:
            message = EmailMultiAlternatives(
                subject="Yessal Gui — test de configuration SMTP",
                body=(
                    "Si vous lisez ceci, la configuration SMTP de Yessal Gui "
                    "fonctionne.\n\n"
                    f"Hôte     : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}\n"
                    f"Compte   : {settings.EMAIL_HOST_USER}\n"
                    f"TLS      : {settings.EMAIL_USE_TLS}\n"
                ),
                from_email=f'{settings.EMAIL_FROM_NAME} <{settings.DEFAULT_FROM_EMAIL}>',
                to=[destinataire],
            )

        try:
            connexion = get_connection(fail_silently=False)
            message.connection = connexion
            envoyes = message.send()
        except Exception as exc:
            # Les erreurs SMTP les plus fréquentes méritent mieux qu'une trace.
            indice = ''
            texte = str(exc).lower()
            if 'username and password not accepted' in texte or '535' in texte:
                indice = (
                    "\n\nGmail refuse le mot de passe du compte : il faut un "
                    "MOT DE PASSE D'APPLICATION (16 caractères), créé sur "
                    "https://myaccount.google.com/apppasswords après avoir "
                    "activé la validation en deux étapes."
                )
            elif 'timed out' in texte or 'connection refused' in texte:
                indice = (
                    "\n\nAucune réponse du serveur : vérifiez le port (587 pour "
                    "TLS, 465 pour SSL) et qu'aucun pare-feu ne bloque la sortie."
                )
            raise CommandError(f"Échec de l'envoi : {exc}{indice}")

        if envoyes:
            self.stdout.write(self.style.SUCCESS(
                f"Message envoyé à {destinataire}. "
                "Vérifiez la boîte de réception ET le dossier « indésirables »."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                "Django rapporte 0 message envoyé, sans erreur. "
                "C'est le comportement du backend console."
            ))
