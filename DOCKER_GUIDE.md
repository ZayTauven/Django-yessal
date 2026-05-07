# Docker Deployment Guide - Yessal Backend

## 📋 Configuration d'environnement

### 1. Créer `.env.local` à partir du template

```bash
cp core/.env.example core/.env.local
```

Puis édite `core/.env.local` avec tes variables réelles :

- `SECRET_KEY` : Une clé secrète Django longue et aléatoire
- `DB_PASSWORD` : Mot de passe PostgreSQL sécurisé
- `ALLOWED_HOSTS` : Domaines autorisés (ex: `localhost,yourdomain.com`)
- `CORS_ALLOWED_ORIGINS` : URLs frontend autorisées
- Credentials de paiement (Bictorys)

**⚠️ IMPORTANT** : `core/.env.local` est ignoré par Git (voir `.gitignore`). Ne le commite jamais.

---

## 🚀 Déploiement

### Mode Développement (Hot Reload)

```bash
# Option 1 : Docker Compose Watch (file sync en temps réel)
docker compose watch

# Option 2 : Docker Compose standard
docker compose up

# Accès : http://localhost:8000
```

**Avantages** :

- Modifications de code appliquées instantanément
- Base de données PostgreSQL isolée
- Environnement identique à la production

### Mode Production

```bash
# Build et lancer avec configuration de production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Voir les logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f web
```

**Caractéristiques** :

- `DEBUG=False`
- Utilise `gunicorn` en production
- Nginx comme reverse proxy
- Healthchecks activés
- Volumes persistants pour DB et média

---

## 🏥 Healthchecks

Les services incluent des healthchecks :

**PostgreSQL** :

```bash
docker compose ps  # Affiche l'état de santé
# "healthy" = ✅, "unhealthy" = ❌
```

**Web (Django)** :

- Nécessite une route `/health/` dans Django
- Ajuste le endpoint dans `docker-compose.yml` si besoin

---

## 📦 Commandes utiles

```bash
# Arrêter les conteneurs
docker compose down

# Arrêter + supprimer les volumes (attention : efface la DB !)
docker compose down -v

# Reconstruire l'image
docker compose build --no-cache

# Voir les logs
docker compose logs -f web
docker compose logs -f db

# Exécuter une commande dans le conteneur
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic

# Redémarrer un service
docker compose restart web
```

---

## 🔒 Sécurité

✅ **Ce qui est protégé** :

- `core/.env.local` ignoré par Git (variables sensibles)
- Utilisateur non-root `appuser` dans le conteneur
- Multi-stage build pour réduire les vulnérabilités
- `.dockerignore` exclut les fichiers sensibles
- Healthchecks pour vérifier la disponibilité

⚠️ **À faire** :

- Générer une vraie `SECRET_KEY` : `python -c "import secrets; print(secrets.token_urlsafe(50))"`
- Utiliser SSL/TLS en production (ajouter certificat dans `nginx/ssl/`)
- Changer les identifiants PostgreSQL par défaut
- Vérifier `ALLOWED_HOSTS` et `CORS_ALLOWED_ORIGINS`

---

## 🐳 Architecture Docker

### `Dockerfile`

- **Stage 1 (Builder)** : Installe les dépendances Python, construit la couche pip
- **Stage 2 (Prod)** : Image finale légère, non-root user, entrypoint script

### `docker-compose.yml`

- **PostgreSQL 16** : Base de données persistante
- **Django Web** : Application Django avec Gunicorn
- Healthchecks intégrés

### `docker-compose.override.yml`

- **Développement** : Django dev server, hot reload, DEBUG=True
- Utilisation automatique : `docker compose up` charge `.override.yml` en local

### `docker-compose.prod.yml`

- **Production** : Gunicorn, Nginx, no hot reload, DEBUG=False
- Commande : `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`

---

## 🔧 Troubleshooting

### "ConnectionRefusedError: Failed to connect to PostgreSQL"

```bash
# Vérifier que le conteneur DB est "healthy"
docker compose ps

# Logs du DB
docker compose logs db

# Attendre 30s (le script entrypoint attend la DB)
docker compose logs -f web | grep "Waiting for database"
```

### "ModuleNotFoundError" après `docker compose up`

```bash
# Reconstruire l'image
docker compose build --no-cache
docker compose up
```

### Permission denied sur `/app/entrypoint.prod.sh`

```bash
# Le Dockerfile exécute `chmod +x` automatiquement
# Si problème persiste, reconstruire
docker compose build --no-cache
```

### Port 8000 déjà utilisé

```bash
# Changer le port dans docker-compose.yml
# Exemple : "8001:8000"
```

---

## 📝 Notes

- Les volumes `postgres_data` et `media_data` sont persistants entre redémarrages
- Migrations Django s'exécutent automatiquement au démarrage (`entrypoint.prod.sh`)
- Fichiers statiques collectés automatiquement au démarrage
- `depends_on` avec `condition: service_healthy` assure que la DB est prête avant Django

---

## 🎯 Prochaines étapes

1. ✅ Créer `core/.env.local`
2. ✅ Créer une route `/health/` dans Django (endpoint simple pour healthcheck)
3. ✅ Lancer `docker compose watch` en dev
4. ✅ Tester `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` en prod
5. ✅ Ajouter Nginx config si nécessaire
