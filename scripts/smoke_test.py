import requests
import sys

BASE_URL = "http://127.0.0.1:8000/api"

ENDPOINTS = [
    "/accounts/profile/",
    "/events/fetes/",
    "/contributions/campaigns/",
    "/news/posts/",
    "/comms/announcements/",
]

def run_smoke_test():
    print("🚀 Démarrage du Smoke Test Yessal Backend...")
    success_count = 0
    
    for endpoint in ENDPOINTS:
        url = f"{BASE_URL}{endpoint}"
        try:
            # On teste juste l'accessibilité (même si 401/403, l'endpoint doit exister)
            response = requests.get(url, timeout=5)
            status = response.status_code
            if status in [200, 401, 403]:
                print(f"✅ {endpoint} : {status} (OK)")
                success_count += 1
            else:
                print(f"❌ {endpoint} : {status} (ERREUR)")
        except Exception as e:
            print(f"💥 {endpoint} : Erreur de connexion ({e})")

    print(f"\n📊 Résultat : {success_count}/{len(ENDPOINTS)} endpoints opérationnels.")
    if success_count < len(ENDPOINTS):
        sys.exit(1)

if __name__ == "__main__":
    run_smoke_test()
