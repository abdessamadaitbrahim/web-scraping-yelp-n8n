"""
    Ville : Paris, France                                         
    Objectif : Top 500 restaurants → reviewers → nationalités     


    INSTALLATION :
    pip install selenium webdriver-manager beautifulsoup4 lxml

    USAGE :
    python yelp_scraper.py
"""

# ─── Imports ──────────────────────────────────────────────────────────────────

import time
import random
import json
import csv
import re
import logging
from collections import Counter
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

# ─── Configuration ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

BASE_URL         = "https://www.yelp.com"
CITY             = "Paris, France"
MAX_RESTAURANTS  = 500
RESULTS_PER_PAGE = 10
REVIEWS_PER_PAGE = 20

# Délai entre chaque restaurant (secondes)
DELAY_BETWEEN_RESTAURANTS = (10, 30)

OUTPUT_DIR      = Path("output")
CSV_FILE        = OUTPUT_DIR / "results_reviewers.csv"
JSON_FILE       = OUTPUT_DIR / "nationality_ranking.json"
CHECKPOINT_FILE = OUTPUT_DIR / "checkpoint.json"

OUTPUT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
#   DRIVER — Réaliste (indiscernable d'un vrai navigateur humain)
# ═══════════════════════════════════════════════════════════════════════════════

# Profils réalistes de navigateurs desktop (UA + viewport associé)
_BROWSER_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "viewport": (1920, 1080), "platform": "Win32",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "viewport": (1536, 864), "platform": "Win32",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "viewport": (1440, 900), "platform": "MacIntel",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
        "viewport": (1680, 1050), "platform": "MacIntel",
    },
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "viewport": (1280, 800), "platform": "Linux x86_64",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
        "viewport": (1600, 900), "platform": "Win32",
    },
]


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """
    Crée un ChromeDriver réaliste :
      • User-Agent desktop authentique (rotation parmi 6 profils)
      • Viewport cohérent avec le profil
      • navigator.webdriver = undefined (patch CDP)
      • navigator.plugins, languages, platform réalistes
      • Suppression des bannières/flags automation Chrome
      • Profil Chrome persistant (cookies, cache entre sessions)
      • Préférences réalistes (langue, notifications refusées)
    """
    profile = random.choice(_BROWSER_PROFILES)
    ua      = profile["ua"]
    w, h    = profile["viewport"]

    options = Options()

    if headless:
        options.add_argument("--headless=new")

    # ── User-Agent réaliste ──
    options.add_argument(f"--user-agent={ua}")

    # ── Viewport cohérent avec le profil ──
    options.add_argument(f"--window-size={w},{h}")

    # ── Supprimer tous les flags automation visibles ──
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    # ── Stabilité & compatibilité ──
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-infobars")

    # ── Préférences réalistes ──
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.notifications": 2,
        "intl.accept_languages": "fr-FR,fr,en-US,en",
        "profile.managed_default_content_settings.images": 1,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })

    # ── Profil persistant (garde cookies/sessions entre exécutions) ──
    profile_dir = Path("chrome_profile")
    profile_dir.mkdir(exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir.resolve()}")

    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=options)

    # ── Patch CDP : masquer toutes les traces Selenium ──
    stealth_js = f"""
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

        Object.defineProperty(navigator, 'plugins', {{
            get: () => {{
                const arr = [
                    {{ name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',                  description: 'Portable Document Format' }},
                    {{ name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',     description: '' }},
                    {{ name: 'Native Client',       filename: 'internal-nacl-plugin',                 description: '' }},
                ];
                arr.__proto__ = PluginArray.prototype;
                return arr;
            }}
        }});

        Object.defineProperty(navigator, 'languages', {{
            get: () => ['fr-FR', 'fr', 'en-US', 'en']
        }});

        Object.defineProperty(navigator, 'platform', {{
            get: () => '{profile["platform"]}'
        }});

        window.chrome = {{
            runtime: {{
                connect: function() {{}},
                sendMessage: function() {{}}
            }}
        }};

        const _origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({{ state: Notification.permission }})
                : _origQuery(p);
    """
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})

    driver.set_window_size(w, h)
    log.info(f"Driver créé — {ua[:65]}... | {w}x{h} | {profile['platform']}")
    return driver


# ═══════════════════════════════════════════════════════════════════════════════
#   HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def safe_get(driver: webdriver.Chrome, url: str, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            time.sleep(2)
            return True
        except WebDriverException as e:
            log.warning(f"[Tentative {attempt}/{retries}] Erreur chargement {url}: {e}")
            if attempt < retries:
                time.sleep(5)
    return False


def get_soup(driver: webdriver.Chrome) -> BeautifulSoup:
    return BeautifulSoup(driver.page_source, "lxml")


# ═══════════════════════════════════════════════════════════════════════════════
#   CHECKPOINT
# ═══════════════════════════════════════════════════════════════════════════════

def save_checkpoint(data: dict):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"Checkpoint sauvegardé ({len(data.get('restaurants', []))} restaurants)")


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"Checkpoint chargé — reprise à partir de {data.get('resume_index', 0)}")
        return data
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
#   ÉTAPE A — Top 500 restaurants
# ═══════════════════════════════════════════════════════════════════════════════

def get_top_restaurants(driver: webdriver.Chrome, city: str, max_restaurants: int = 500) -> list[dict]:
    restaurants = []
    offset = 0

    log.info(f"\n{'='*60}")
    log.info(f"  ÉTAPE A : Top {max_restaurants} restaurants — {city}")
    log.info(f"{'='*60}")

    while len(restaurants) < max_restaurants:
        url = (
            f"{BASE_URL}/search"
            f"?find_desc=Restaurants"
            f"&find_loc={city.replace(' ', '+').replace(',', '%2C')}"
            f"&start={offset}"
            f"&sortby=rating"
        )

        log.info(f"  Page offset={offset} | Total={len(restaurants)}")
        if not safe_get(driver, url):
            log.error("Impossible de charger la page de recherche.")
            break

        # ── Pause CAPTCHA manuelle uniquement sur la première page ──
        if offset == 0:
            import threading
            print("\n" + "="*60)
            print("  ⏳ CAPTCHA : Résolvez manuellement si nécessaire.")
            print("  Le scraping démarrera automatiquement dans 60 secondes.")
            print("  Appuyez sur Entrée pour démarrer immédiatement.")
            print("="*60)
            early_start = threading.Event()
            def _wait_enter():
                input()
                early_start.set()
            t = threading.Thread(target=_wait_enter, daemon=True)
            t.start()
            for remaining in range(60, 0, -1):
                if early_start.is_set():
                    break
                print(f"\r  Démarrage dans {remaining:>2}s...", end="", flush=True)
                time.sleep(1)
            print("\n  ✓ Démarrage du scraping !\n")

        soup = get_soup(driver)
        cards = soup.select("div[data-testid='serp-ia-card']")

        if not cards:
            log.warning(f"  Aucune carte restaurant trouvée (offset={offset}). Fin pagination.")
            break

        new_found = 0
        for card in cards:
            if len(restaurants) >= max_restaurants:
                break

            name_tag = card.select_one("div[data-traffic-crawl-id='SearchResultBizName'] h3 a")
            if not name_tag:
                name_tag = card.select_one("h3 a[href*='/biz/']")
            if not name_tag:
                continue

            raw_name = name_tag.get_text(strip=True)
            href = name_tag.get("href", "")
            rest_url = BASE_URL + href if href.startswith("/") else href
            name = re.sub(r"^\d+\.\s*\xa0?", "", raw_name).strip()

            rating_tag = card.select_one("div[aria-label*='star']")
            rating = "N/A"
            if rating_tag:
                aria = rating_tag.get("aria-label", "")
                m = re.search(r"([\d.]+)", aria)
                rating = m.group(1) if m else "N/A"

            review_count = "0"
            rating_container = card.select_one("div[data-traffic-crawl-id='SearchResultBizRating']")
            if rating_container:
                spans = rating_container.select("span")
                for span in spans:
                    text = span.get_text(strip=True)
                    if "review" in text.lower() or re.search(r"\([\d,.k]+\)", text):
                        review_count = text.strip("()")
                        break

            if not any(r["url"] == rest_url for r in restaurants):
                restaurants.append({
                    "name": name,
                    "url": rest_url,
                    "rating": rating,
                    "review_count": review_count,
                })
                new_found += 1
                log.info(f"    [{len(restaurants):>3}] {name} — {rating}★ ({review_count} avis)")

        if new_found == 0:
            log.warning("  Aucun nouveau restaurant sur cette page. Arrêt.")
            break

        offset += RESULTS_PER_PAGE

    log.info(f"\n  ✓ {len(restaurants)} restaurants collectés.\n")
    return restaurants


# ═══════════════════════════════════════════════════════════════════════════════
#   ÉTAPES B + C — Reviewers et nationalité
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_reviewers_from_soup(soup: BeautifulSoup, seen_users: set) -> tuple[list[dict], int]:
    """
    Parse tous les blocs de review du HTML réel Yelp.
    Structure confirmée : ul.list__09f24__gE1oR > li > ... > a[href*=/user_details]
    Localisation : div[data-testid=UserPassportInfoTextContainer] > span
    """
    found = []

    # Sélecteur principal confirmé par le HTML réel
    blocks = soup.select("ul.list__09f24__gE1oR > li")

    # Fallback : tous les <li> contenant un lien user_details
    if not blocks:
        blocks = [
            li for li in soup.select("li")
            if li.select_one("a[href*='/user_details']")
        ]

    for block in blocks:
        user_link = block.select_one("a[href*='/user_details']")
        if not user_link:
            continue

        username    = user_link.get_text(strip=True)
        href        = user_link.get("href", "")
        if not href:
            continue
        profile_url = BASE_URL + href if href.startswith("/") else href

        if profile_url in seen_users:
            continue
        seen_users.add(profile_url)

        # ── Localisation (sélecteur exact du HTML Yelp) ──
        # Structure : div[data-testid="UserPassportInfoTextContainer"] > span
        nationality = "Inconnu"
        loc_container = block.select_one("div[data-testid='UserPassportInfoTextContainer']")
        if loc_container:
            loc_span = loc_container.select_one("span")
            if loc_span:
                raw = loc_span.get_text(strip=True)
                # Format : "Ville, Région, Pays"  ou  "Ville, Pays"  ou juste "Pays"
                # Ex: "Seattle, WA" → WA (US state)
                # Ex: "The Beach, Toronto, Canada" → Canada
                # Ex: "Ottawa, Canada" → Canada
                if raw:
                    parts = [p.strip() for p in raw.split(",")]
                    nationality = parts[-1] if parts else "Inconnu"

        found.append({
            "username":    username,
            "profile_url": profile_url,
            "nationality": nationality,
        })

    return found, len(blocks)


def _get_next_page_url(soup: BeautifulSoup):
    """
    Extrait l'URL de la page suivante depuis le HTML Yelp réel.
    Structure confirmée :
      <a href="/biz/tradi-paris?osq=Restaurants&start=10"
         class="next-link navigation-button__09f24__SUbs2 ..."
         aria-label="Next">
    """
    # Sélecteur exact du HTML Yelp
    next_a = soup.select_one("a.next-link[aria-label='Next']")
    if next_a:
        href = next_a.get("href", "")
        if href:
            return BASE_URL + href if href.startswith("/") else href

    # Fallback : chercher tout lien avec start= dans la pagination
    pagination = soup.select_one("div.pagination__09f24__g1UxQ, div[aria-label='Pagination navigation']")
    if pagination:
        for a in pagination.select("a[href*='start=']"):
            label = a.get("aria-label", "")
            if "next" in label.lower():
                href = a.get("href", "")
                return BASE_URL + href if href.startswith("/") else href

    return None


def get_reviewers(driver: webdriver.Chrome, restaurant: dict) -> list[dict]:
    """
    Récupère TOUS les reviewers d'un restaurant en suivant la pagination réelle Yelp.
    La pagination utilise ?start=10, ?start=20, etc.
    Le lien "Next" est dans : a.next-link[aria-label="Next"]
    """
    reviewers  = []
    seen_users = set()
    url = restaurant["url"]
    if not url or not url.startswith("http"):
        return reviewers

    current_url = url.split("?")[0].rstrip("/")
    page_num    = 0
    MAX_PAGES   = 50  # sécurité anti-boucle infinie

    log.info(f"  Pagination reviews : {restaurant['name']}")

    while page_num < MAX_PAGES:
        if not safe_get(driver, current_url):
            break

        # Scroll pour charger les reviews lazy
        _scroll_reviews_into_view(driver)

        soup = get_soup(driver)
        new_reviewers, total_blocks = _extract_reviewers_from_soup(soup, seen_users)

        log.info(f"    Page {page_num + 1} ({current_url.split('?')[-1] if '?' in current_url else 'start=0'}) → {len(new_reviewers)} nouveaux / {total_blocks} blocs")

        if new_reviewers:
            reviewers.extend(new_reviewers)

        # ── Chercher le lien "Next" dans le HTML ──
        next_url = _get_next_page_url(soup)
        if not next_url:
            log.info(f"    → Dernière page atteinte (total: {len(reviewers)} reviewers).")
            break

        current_url = next_url
        page_num   += 1
        time.sleep(random.uniform(2, 5))

    return reviewers


def _scroll_reviews_into_view(driver: webdriver.Chrome):
    """Scroll progressif pour déclencher le chargement lazy des reviews."""
    for _ in range(4):
        driver.execute_script("window.scrollBy(0, 600);")
        time.sleep(random.uniform(0.4, 0.9))
    driver.execute_script("window.scrollBy(0, -200);")
    time.sleep(0.5)


# ═══════════════════════════════════════════════════════════════════════════════
#   ÉTAPE D — Classement des nationalités
# ═══════════════════════════════════════════════════════════════════════════════

def rank_nationalities(nationalities: list[str]) -> list[dict]:
    counter = Counter(n for n in nationalities if n and n != "Inconnu")
    total = sum(counter.values())
    ranking = []
    for rank, (nat, count) in enumerate(counter.most_common(), 1):
        ranking.append({
            "rank": rank,
            "nationality": nat,
            "count": count,
            "percentage": round(count / total * 100, 2) if total else 0,
        })
    return ranking


def print_ranking(ranking: list[dict]):
    print(f"\n{'═'*65}")
    print(f"  CLASSEMENT DES NATIONALITÉS LES PLUS ACTIVES — {CITY}")
    print(f"{'═'*65}")
    print(f"  {'#':<5} {'Nationalité':<25} {'Reviews':>8}  {'%':>6}  Barre")
    print(f"  {'─'*60}")

    max_count = ranking[0]["count"] if ranking else 1
    for r in ranking:
        bar = "█" * int(r["count"] / max_count * 20)
        print(f"  {r['rank']:<5} {r['nationality']:<25} {r['count']:>8}  {r['percentage']:>5.1f}%  {bar}")

    total = sum(r["count"] for r in ranking)
    print(f"\n  Total reviews analysées : {total}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#   EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def export_csv(results: list[dict]):
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["restaurant", "username", "nationality"])
        writer.writeheader()
        writer.writerows(results)
    log.info(f"✓ CSV exporté : {CSV_FILE} ({len(results)} lignes)")


def export_json(ranking: list[dict]):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(ranking, f, ensure_ascii=False, indent=2)
    log.info(f"✓ JSON exporté : {JSON_FILE} ({len(ranking)} nationalités)")


# ═══════════════════════════════════════════════════════════════════════════════
#   PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def run():
    log.info("Démarrage du scraper Yelp")

    checkpoint = load_checkpoint()
    resume_index = checkpoint.get("resume_index", 0)
    all_results = checkpoint.get("results", [])
    all_nationalities = [r["nationality"] for r in all_results]

    driver = create_driver(headless=False)

    try:
        safe_get(driver, BASE_URL)

        # ── ÉTAPE A : Top restaurants ──
        if not checkpoint.get("restaurants"):
            restaurants = get_top_restaurants(driver, CITY, MAX_RESTAURANTS)
            save_checkpoint({"restaurants": restaurants, "resume_index": 0, "results": []})
        else:
            restaurants = checkpoint["restaurants"]
            log.info(f"Restaurants chargés depuis checkpoint : {len(restaurants)}")

        # ── ÉTAPES B + C : Reviewers & nationalités ──
        log.info(f"\n{'='*60}")
        log.info(f"  ÉTAPES B & C : Reviewers et nationalités")
        log.info(f"{'='*60}\n")

        for i, restaurant in enumerate(restaurants[resume_index:], start=resume_index + 1):
            log.info(f"\n[{i}/{len(restaurants)}] {restaurant['name']}")

            reviewers = get_reviewers(driver, restaurant)
            log.info(f"  → {len(reviewers)} reviewers trouvés")

            for reviewer in reviewers:
                nat = reviewer["nationality"]
                all_nationalities.append(nat)
                all_results.append({
                    "restaurant": restaurant["name"],
                    "username": reviewer["username"],
                    "nationality": nat,
                })
                log.info(f"    {reviewer['username']} → {nat}")

            save_checkpoint({
                "restaurants": restaurants,
                "resume_index": i,
                "results": all_results,
            })

            # ── Pause 10–30 secondes entre chaque restaurant ──
            pause = random.uniform(*DELAY_BETWEEN_RESTAURANTS)
            log.info(f"  ⏸  Pause {pause:.1f}s avant prochain restaurant...")
            time.sleep(pause)

        # ── ÉTAPE D : Classement ──
        ranking = rank_nationalities(all_nationalities)
        print_ranking(ranking)

        export_csv(all_results)
        export_json(ranking)

        log.info("\n✅ Scraping terminé avec succès !")
        return ranking

    except KeyboardInterrupt:
        log.warning("\n⛔ Interruption clavier. Checkpoint sauvegardé, relancez le script.")
    except Exception as e:
        log.error(f"Erreur inattendue : {e}", exc_info=True)
    finally:
        driver.quit()
        log.info("Driver fermé.")


if __name__ == "__main__":
    run()