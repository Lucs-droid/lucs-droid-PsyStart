"""PsiStart – monitorizarea surselor oficiale.

Rulează zilnic din GitHub Actions:
1. Verifică fiecare document marcat "watch": true din data/resources.json.
   - dacă linkul nu mai răspunde -> marchează eroare
   - dacă textul s-a schimbat -> marchează "modificat" și deschide un issue pe GitHub
2. Citește pagina principală copsi.ro și reține documentele noi apărute (data/noutati.json).
3. Scrie data/status.json, pe care pagina îl afișează ("verificat automat la ...").

Nu modifică niciodată fișele (resources.json). Decizia de actualizare rămâne la om.
"""
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SNAP = ROOT / "state" / "snapshots"
UA = "PsiStart-monitor/1.0 (ghid independent; verificare zilnica)"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
COPSI_HOME = "https://www.copsi.ro/index.php"


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TextAndLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.links, self._skip, self._href, self._atext = [], [], 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a":
            self._href, self._atext = dict(attrs).get("href"), []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._atext).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._href:
            self._atext.append(data.strip())


def to_text(resp):
    ctype = resp.headers.get("content-type", "").lower()
    if "pdf" in ctype or resp.url.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(resp.content))
            return "\n".join((p.extract_text() or "") for p in reader.pages), None
        except Exception:  # PDF ilizibil: comparăm octeții
            return None, hashlib.sha256(resp.content).hexdigest()
    p = TextAndLinks()
    p.feed(resp.text)
    return "\n".join(p.text), None


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def fetch(url):
    return requests.get(url, headers={"User-Agent": UA}, timeout=60)


def summarize(title, old, new):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not old:
        return None
    prompt = (
        f"Documentul oficial „{title}” s-a modificat. Compară cele două versiuni și spune, în română, "
        "în maximum 5 puncte, CE s-a schimbat concret (sume, termene, documente cerute, condiții). "
        "Nu interpreta juridic, nu da sfaturi. Dacă schimbarea e doar de formatare, spune asta.\n\n"
        f"<versiune_veche>\n{old[:20000]}\n</versiune_veche>\n<versiune_noua>\n{new[:20000]}\n</versiune_noua>"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages", timeout=120,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("SUMMARY_MODEL") or "claude-sonnet-5-5", "max_tokens": 800,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception as e:
        print(f"  rezumat AI indisponibil: {e}")
        return None


def open_issue(title, body):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(f"  (local) issue: {title}")
        return
    api = f"https://api.github.com/repos/{repo}/issues"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    # nu dublăm un issue deja deschis pentru același document
    existing = requests.get(api, headers=h, params={"state": "open", "labels": "sursa-modificata"}, timeout=30)
    if existing.ok and any(i["title"] == title for i in existing.json()):
        print("  issue deja deschis")
        return
    requests.post(api, headers=h, timeout=30,
                  json={"title": title, "body": body, "labels": ["sursa-modificata"]})


def check_sources(resources, status):
    for item in resources["items"]:
        if not item.get("watch") or not item.get("url"):
            continue
        iid, url = item["id"], item["url"]
        prev = status["items"].get(iid, {})
        entry = {**prev, "checked_at": NOW}
        print(f"- {iid}")
        try:
            resp = fetch(url)
        except Exception as e:
            entry.update(state="eroare", error=str(e)[:200])
            status["items"][iid] = entry
            continue
        if resp.status_code != 200:
            entry.update(state="eroare", error=f"HTTP {resp.status_code}")
            status["items"][iid] = entry
            if prev.get("state") != "eroare":
                open_issue(f"Link indisponibil: {item['title']}",
                           f"Linkul nu mai răspunde ({resp.status_code}).\n\n{url}\n\nVerifică dacă documentul a fost mutat sau înlocuit.")
            continue
        text, raw_hash = to_text(resp)
        digest = raw_hash or hashlib.sha256(normalize(text).encode()).hexdigest()
        snap = SNAP / f"{iid}.txt"
        old_text = snap.read_text(encoding="utf-8") if snap.exists() else None
        if prev.get("hash") and prev["hash"] != digest:
            entry.update(state="modificat", changed_at=NOW)
            summary = summarize(item["title"], old_text, text or "")
            body = (f"Documentul oficial s-a modificat față de ultima verificare.\n\n**Link:** {url}\n\n"
                    + (f"**Rezumat automat (de verificat):**\n\n{summary}\n\n" if summary else "")
                    + "**Ce ai de făcut:** deschide documentul, verifică fișa din PsiStart și actualizează "
                    "`data/resources.json` (inclusiv câmpul `verified`). Apoi închide acest issue.")
            open_issue(f"Sursă modificată: {item['title']}", body)
        else:
            entry["state"] = "ok" if prev.get("state") != "modificat" else "modificat"
        entry.update(hash=digest, last_ok=NOW, error=None)
        if text is not None:
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(text, encoding="utf-8")
        status["items"][iid] = entry


def check_news():
    """Documente noi publicate pe pagina principală copsi.ro."""
    path = DATA / "noutati.json"
    news = load(path, {"seen": [], "items": []})
    try:
        resp = fetch(COPSI_HOME)
        resp.raise_for_status()
    except Exception as e:
        print(f"copsi.ro indisponibil: {e}")
        return False
    p = TextAndLinks()
    p.feed(resp.text)
    first_run = not news["seen"]
    seen = set(news["seen"])
    for href, label in p.links:
        full = urljoin(COPSI_HOME, href)
        if not re.search(r"\.(pdf|docx?|xlsx?)($|\?)", full, re.I) or full in seen:
            continue
        seen.add(full)
        if not first_run:  # la prima rulare doar reținem ce există deja
            name = label or Path(full).stem.replace("_", " ")
            news["items"].insert(0, {"title": name[:200], "url": full, "found_at": NOW})
    news["seen"] = sorted(seen)
    news["items"] = news["items"][:60]
    save(path, news)
    return True


def main():
    resources = load(DATA / "resources.json", None)
    if not resources:
        sys.exit("data/resources.json lipsește sau e invalid")
    status = load(DATA / "status.json", {"items": {}})
    check_sources(resources, status)
    status["copsi_reachable"] = check_news()
    status["checked_at"] = NOW
    save(DATA / "status.json", status)
    print("Gata.")


if __name__ == "__main__":
    main()
