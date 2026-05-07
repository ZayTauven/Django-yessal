# Guide d'intégration et de gestion du Backend Dockerisé - Yessal

## 📚 Table des matières

1. [Flux de travail quotidien](#flux-de-travail-quotidien)
2. [Modifications de code](#modifications-de-code)
3. [Migrations de base de données](#migrations-de-base-de-données)
4. [Gestion des dépendances](#gestion-des-dépendances)
5. [Tests et debugging](#tests-et-debugging)
6. [Commandes utiles](#commandes-utiles)
7. [Troubleshooting](#troubleshooting)

---

## 🔄 Flux de travail quotidien

### Démarrage du développement

```bash
# Terminal 1 : Lancer les conteneurs avec hot reload
cd yessal-backend
docker compose up -d

# Vérifier que tout fonctionne
docker compose ps
# Output :
# NAME                COMMAND                 SERVICE    STATUS
# yessal-backend-db-1  "docker-entrypoint..."  db         Up (healthy)
# yessal-backend-web-1 "gunicorn..."           web        Up (healthy)

# Voir les logs en temps réel
docker compose logs -f web
```

### Pendant le développement (avec hot reload optionnel)

```bash
# Option 1 : Docker Compose Watch (sync en temps réel)
docker compose watch

# Option 2 : Développement classique (sans hot reload)
docker compose up -d
# Puis modifier le code normalement
```

### Arrêt à la fin de la journée

```bash
# Garder les données (recommandé)
docker compose down

# OU : Garder les conteneurs actifs (pour redémarrage rapide)
docker compose stop
```

---

## 💻 Modifications de code

### Scénario 1 : Modification simple d'une vue/logique

**Exemple** : Tu modifies `accounts/views.py`

```bash
# 1. Édite le fichier normalement dans VS Code
# accounts/views.py : modifie une fonction

# 2. Les changements se reflètent IMMÉDIATEMENT
# - Si hot reload (docker compose watch) : Sync automatique
# - Sinon : Rafraîchis http://localhost:8000 dans le navigateur

# 3. Si besoin de redémarrer le serveur Django :
docker compose restart web

# 4. Vérifier les logs pour les erreurs
docker compose logs web --tail=20
```

### Scénario 2 : Ajout d'une nouvelle application Django

**Exemple** : Tu créas une nouvelle app `analytics`

```bash
# 1. Crée l'app
docker compose exec web python manage.py startapp analytics

# 2. Ajoute l'app à INSTALLED_APPS dans core/settings.py
# INSTALLED_APPS = [
#     ...
#     'analytics',
# ]

# 3. Redémarre le serveur
docker compose restart web

# 4. Vérifier
docker compose logs web --tail=10
```

### Scénario 3 : Modification des variables d'environnement

**Exemple** : Tu changes `DEBUG`, `ALLOWED_HOSTS`, ou une clé API

```bash
# 1. Édite core/.env.local
nano core/.env.local
# Ou ouvre-le dans VS Code

# 2. Change la valeur
# DEBUG=True
# ALLOWED_HOSTS=localhost,127.0.0.1,mydomain.com

# 3. Redémarre les conteneurs pour recharger les variables
docker compose restart web
docker compose restart db  # Si c'est une variable DB

# 4. Vérifier
docker compose logs web --tail=5
```

### Scénario 4 : Ajouter des fichiers statiques (CSS, JS, images)

```bash
# 1. Place les fichiers dans le dossier statique
# Structure :
# yessal-backend/
#   ├── core/
#   ├── static/
#   │   ├── css/
#   │   ├── js/
#   │   └── images/

# 2. Collecte les fichiers statiques (si besoin)
docker compose exec web python manage.py collectstatic --noinput

# 3. Les fichiers sont automatiquement servis à http://localhost:8000/static/
```

---

## 🗄️ Migrations de base de données

### Scénario 1 : Modifier un modèle existant (ajouter un champ)

**Exemple** : Tu ajoutes un champ `phone` au modèle `User`

```python
# accounts/models.py
class User(AbstractUser):
    phone = models.CharField(max_length=20, blank=True, null=True)  # ← Nouveau champ
    email = models.EmailField(unique=True)
```

**Commandes** :

```bash
# 1. Crée la migration (Django détecte le changement)
docker compose exec web python manage.py makemigrations

# Output : "Migrations for 'accounts': 0002_user_phone.py"

# 2. Vérifie la migration créée (optionnel)
docker compose exec web python manage.py migrate --plan

# Output :
# Planned operations:
#   accounts.0002_user_phone ... (add field)

# 3. Applique la migration
docker compose exec web python manage.py migrate

# Output : "Running migrations: accounts.0002_user_phone ... OK"

# 4. Vérifier
docker compose logs web --tail=5
```

### Scénario 2 : Créer un nouveau modèle

**Exemple** : Tu ajoutes un modèle `BlogPost` dans une nouvelle app

```bash
# 1. Crée l'app
docker compose exec web python manage.py startapp blog

# 2. Ajoute à INSTALLED_APPS dans core/settings.py

# 3. Définir le modèle
# blog/models.py
from django.db import models

class BlogPost(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

# 4. Crée la migration
docker compose exec web python manage.py makemigrations blog

# 5. Applique la migration
docker compose exec web python manage.py migrate

# 6. Vérifier
docker compose logs web
```

### Scénario 3 : Revenir en arrière (rollback)

```bash
# 1. Voir l'historique des migrations
docker compose exec web python manage.py showmigrations accounts

# Output :
# accounts
#   [X] 0001_initial
#   [X] 0002_user_phone
#   [ ] 0003_add_address

# 2. Revenir à une migration spécifique
docker compose exec web python manage.py migrate accounts 0001_initial

# 3. Vérifier
docker compose exec web python manage.py showmigrations accounts
```

### Scénario 4 : Créer une migration vide (données personnalisées)

```bash
# 1. Créer une migration vide
docker compose exec web python manage.py makemigrations --empty accounts --name populate_data

# 2. Éditer le fichier généré
# accounts/migrations/0003_populate_data.py

# 3. Ajouter du code personnalisé (ex: données initiales)
from django.db import migrations

def populate_data(apps, schema_editor):
    LDD = apps.get_model('accounts', 'LDD')
    LDD.objects.create(code='LDD001', name='Example')

class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0002_previous_migration'),
    ]

    operations = [
        migrations.RunPython(populate_data),
    ]

# 4. Appliquer
docker compose exec web python manage.py migrate
```

---

## 📦 Gestion des dépendances

### Ajouter un nouveau package

**Exemple** : Tu veux ajouter `django-filter` pour les filtres avancés

```bash
# 1. Installer localement dans ton venv
.venv\Scripts\pip.exe install django-filter

# 2. Ajouter à requirements.txt
pip freeze | grep django-filter >> requirements.txt
# Ou édite manuellement et ajoute "django-filter==X.X.X"

# 3. Reconstruire l'image Docker pour inclure la nouvelle dépendance
docker compose build --no-cache

# 4. Relancer
docker compose up -d

# 5. Vérifier que le package est installé
docker compose exec web python -c "import django_filters; print(django_filters.__version__)"
```

### Mettre à jour un package

```bash
# 1. Mettre à jour localement
.venv\Scripts\pip.exe install --upgrade django

# 2. Regénérer requirements.txt
.venv\Scripts\pip.exe freeze > requirements.txt

# 3. Reconstruire l'image
docker compose build --no-cache

# 4. Relancer
docker compose up -d

# 5. Vérifier
docker compose logs web --tail=10
```

### Supprimer un package

```bash
# 1. Désinstaller localement
.venv\Scripts\pip.exe uninstall package_name

# 2. Regenerer requirements.txt
.venv\Scripts\pip.exe freeze > requirements.txt

# 3. Reconstruire l'image
docker compose build --no-cache

# 4. Relancer
docker compose up -d
```

---

## 🧪 Tests et debugging

### Exécuter les tests

```bash
# Tous les tests
docker compose exec web python manage.py test

# Tests d'une app spécifique
docker compose exec web python manage.py test accounts

# Tests d'un module spécifique
docker compose exec web python manage.py test accounts.tests.UserTests

# Avec verbose
docker compose exec web python manage.py test accounts -v 2
```

### Django Shell (pour tester du code)

```bash
# Ouvrir le shell Django
docker compose exec web python manage.py shell

# Puis dans le shell :
>>> from accounts.models import User
>>> users = User.objects.all()
>>> print(users.count())
>>> user = users.first()
>>> user.email
>>> exit()
```

### Afficher les requêtes SQL

```bash
# Dans Django settings, ajoute pendant le debug :
# core/settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}

# Puis relance
docker compose restart web
docker compose logs -f web
```

### Déboguer avec print()

```python
# Dans ton code Django
def my_view(request):
    print("DEBUG: Request received")  # ← Ceci s'affiche dans les logs
    return JsonResponse({'status': 'ok'})

# Pour voir en temps réel :
docker compose logs -f web

# Output :
# web-1 | DEBUG: Request received
# web-1 | [07/May/2026 17:34:21] "GET /api/... HTTP/1.1" 200
```

---

## 🛠️ Commandes utiles

### Gestion des conteneurs

```bash
# Voir l'état des conteneurs
docker compose ps

# Voir tous les logs
docker compose logs

# Logs en temps réel
docker compose logs -f

# Logs d'un service spécifique
docker compose logs web
docker compose logs db

# Dernier N lignes
docker compose logs --tail=50 web

# Arrêter les conteneurs (données persistantes)
docker compose stop

# Redémarrer les conteneurs
docker compose restart

# Arrêter et supprimer les conteneurs
docker compose down

# Arrêter + supprimer les volumes (ATTENTION : efface la DB !)
docker compose down -v

# Redémarrer un service
docker compose restart web
```

### Accès à la base de données

```bash
# Accéder à PostgreSQL directement
docker compose exec db psql -U postgres -d yessal_database

# Puis dans psql :
# \dt              → lister les tables
# \d accounts_user → voir la structure d'une table
# SELECT * FROM accounts_user; → voir les données
# \q              → quitter

# Ou via Python :
docker compose exec web python manage.py shell
>>> from django.db import connection
>>> cursor = connection.cursor()
>>> cursor.execute("SELECT * FROM accounts_user;")
>>> cursor.fetchall()
```

### Fichiers et volumes

```bash
# Copier un fichier du conteneur à ta machine
docker compose exec web cat media/file.pdf > ~/Downloads/file.pdf

# Copier un fichier de ta machine au conteneur
docker cp ~/file.pdf yessal-backend-web-1:/app/media/

# Voir les volumes
docker volume ls

# Nettoyer les volumes inutilisés
docker volume prune
```

---

## 🚨 Troubleshooting

### "Service web is not running"

```bash
# Cause 1 : Conteneur arrêté
docker compose ps
docker compose up -d

# Cause 2 : Erreur dans le code
docker compose logs web --tail=50

# Cause 3 : Problème de migration
docker compose exec web python manage.py migrate
docker compose restart web
```

### "ConnectionRefusedError: Can't connect to PostgreSQL"

```bash
# Cause 1 : DB n'est pas prête
docker compose ps
# Attendre que "db" soit "healthy"

# Cause 2 : Mauvaises identifiants
docker compose exec web python manage.py dbshell
# Si erreur : vérifier core/.env.local

# Cause 3 : DB corrompue
docker compose down -v  # ⚠️ Supprime les données !
docker compose up -d
docker compose exec web python manage.py migrate
```

### "ModuleNotFoundError: No module named 'xxx'"

```bash
# Cause : Dépendance manquante
docker compose exec web pip install xxx

# Pour le rendre permanent :
.venv\Scripts\pip.exe install xxx
pip freeze > requirements.txt
docker compose build --no-cache
docker compose up -d
```

### "Port 8000 already in use"

```bash
# Option 1 : Arrêter le conteneur
docker compose down

# Option 2 : Changer le port dans docker-compose.yml
# ports:
#   - "8001:8000"  ← Nouveau port

# Option 3 : Trouver qui utilise le port
netstat -ano | findstr :8000  # Windows
lsof -i :8000                 # Linux/Mac
```

### "Static files not found" (404 on CSS/JS)

```bash
# Collecter les fichiers statiques
docker compose exec web python manage.py collectstatic --noinput

# Vérifier qu'ils sont collectés
docker compose exec web ls -la staticfiles/

# Vérifier STATIC_URL et STATIC_ROOT dans settings.py
```

### Les migrations ne s'appliquent pas

```bash
# Voir l'état des migrations
docker compose exec web python manage.py showmigrations

# Appliquer les migrations manuellement
docker compose exec web python manage.py migrate

# Voir les erreurs
docker compose logs web --tail=50
```

### "Permission denied" ou "appuser can't write"

```bash
# Cause : Problème de permissions du conteneur
# Solution : Reconstruire l'image
docker compose build --no-cache
docker compose up -d

# Ou : Donner les permissions manuellement
docker compose exec web chmod -R 755 media/
```

---

## 📋 Checklist quotidienne

### Au démarrage de la journée

- [ ] `docker compose ps` → Vérifier que tous les services sont "healthy"
- [ ] `docker compose logs web --tail=10` → Vérifier qu'il n'y a pas d'erreurs

### Pendant le développement

- [ ] Éditer le code normalement dans VS Code
- [ ] `docker compose logs -f web` → Voir les logs en temps réel
- [ ] `docker compose restart web` → Si besoin de redémarrer

### Après une modification de modèle

- [ ] `docker compose exec web python manage.py makemigrations`
- [ ] `docker compose exec web python manage.py migrate`
- [ ] `docker compose restart web`

### Avant de commit

- [ ] `docker compose exec web python manage.py test` → Tous les tests passent ?
- [ ] `docker compose logs web` → Pas d'erreurs ?
- [ ] Vérifier les modifications : `git diff`

### À la fin de la journée

- [ ] `docker compose down` → Arrêter les conteneurs
- [ ] `git add .` → Commiter les changements
- [ ] `git push` → Pousser vers le repo

---

## 🎯 Résumé des commandes essentielles

```bash
# Démarrer
docker compose up -d

# Arrêter
docker compose down

# Voir l'état
docker compose ps

# Voir les logs
docker compose logs -f web

# Créer une migration
docker compose exec web python manage.py makemigrations

# Appliquer une migration
docker compose exec web python manage.py migrate

# Redémarrer un service
docker compose restart web

# Exécuter une commande custom
docker compose exec web python manage.py <command>

# Acceder au shell Django
docker compose exec web python manage.py shell
```

---

**Note finale** : Ce guide couvre 90% des cas d'usage. Pour des besoins avancés (déploiement cloud, load balancing, etc.), consulte la documentation Docker officielle.
