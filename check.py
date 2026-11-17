#!/usr/bin/env python3
"""Relevé d'affluence au Mataf : capture le direct télévisé de Makkah, fait estimer la foule sur les images, met à jour live.json.

Lancé toutes les 30 min par GitHub Actions (.github/workflows/releve.yml), sans aucun ordinateur allumé. `check.py --dry` : live.json n'est pas modifié.
live.json : {"now": dernier relevé, "day": {"date", "points": [{"min", "level"}]}} — les formes que lit web/index.html (obs, obsDay).
"""
import datetime, json, pathlib, shutil, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).parent
OUT = HERE / "live.json"
DIRECT = "http://m.live.net.sa:1935/live/quran/gmswf.m3u8"  # Quran TV, direct de la Mosquée sacrée, flux du diffuseur saoudien
YOUTUBE = "https://www.youtube.com/channel/UCos52azQNBgW63_9uDJoPDA/live"  # la même chaîne, en secours : YouTube refuse les machines des hébergeurs
FRAMES = 6  # une image toutes les 10 s : la régie change de caméra, plusieurs plans valent mieux qu'un
tool = lambda name: shutil.which(name) or "/opt/homebrew/bin/" + name  # launchd (Mac) n'a pas le PATH du terminal

PROMPT = """Lis les images frame-1.jpg à frame-%d.jpg de ce dossier : ce sont des captures du direct télévisé de la Mosquée sacrée de Makkah, prises à 10 secondes d'écart.
Estime l'affluence sur le Mataf (l'esplanade autour de la Ka‘bah) à partir des seules images où on le voit vraiment (ignore les gros plans, l'imam, les cartons de texte, les plans d'autres lieux).
Échelle de 0 à 1 : 0,20 = Mataf clairsemé, dalles bien visibles ; 0,55 = foule autour de la Ka‘bah mais premiers plans et bords dégagés ; 0,72 = dense, bords plus lâches ; 0,80 = plein jusqu'aux bords ; 0,95 = saturé, étages compris.
rings : même échelle près de la Ka‘bah (inner), à mi-distance (middle) et sur les bords (outer) ; null pour un anneau qu'aucune image ne montre.
usable : nombre d'images où le Mataf est visible. Si usable vaut 0, mets level à null.
note : une phrase courte en français décrivant ce qu'on voit (ex. « Mataf dense, bords plus fluides »), sans chiffre inventé.
prayer : true si les fidèles sont alignés en rangs de prière (la foule ne tourne pas)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "usable": {"type": "integer"},
        "level": {"type": ["number", "null"]},
        "rings": {"type": "object", "properties": {k: {"type": ["number", "null"]} for k in ("inner", "middle", "outer")}, "required": ["inner", "middle", "outer"]},
        "note": {"type": "string"},
        "prayer": {"type": "boolean"},
    },
    "required": ["usable", "level", "rings", "note", "prayer"],
}


def youtube():
    """Adresse du flux YouTube, ou celle du diffuseur si YouTube refuse (ou sans yt-dlp)."""
    try:
        return subprocess.run([tool("yt-dlp"), "--no-warnings", "-g", "-f", "best[height<=720]/best", YOUTUBE], capture_output=True, text=True, timeout=120, check=True).stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return DIRECT


def capture(dir):
    """Enregistre frame-1.jpg … frame-n.jpg et renvoie n. Une prise qui se bloque est abandonnée (le flux se fige parfois) et on passe sur YouTube."""
    url, n = DIRECT, 0
    for i in range(FRAMES):
        if i:
            time.sleep(10)
        try:
            # une image à la fois, prise à la fin du direct : en continu ffmpeg reste bloqué sur la liste de lecture
            subprocess.run([tool("ffmpeg"), "-loglevel", "error", "-y", "-live_start_index", "-1", "-i", url, "-vf", "scale=960:-2", "-frames:v", "1", str(dir / f"frame-{n + 1}.jpg")], timeout=20, check=True)
            n += 1
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            url = youtube()
    return n


def analyse(dir, n):
    r = subprocess.run(
        [tool("claude"), "-p", PROMPT % n, "--model", "sonnet", "--tools", "Read", "--allowedTools", "Read", "--strict-mcp-config", "--disable-slash-commands",
         "--no-session-persistence", "--setting-sources", "project", "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
        cwd=dir, capture_output=True, text=True, timeout=300, check=True)
    return json.loads(r.stdout)["structured_output"]


def merge(old, a, start, end):
    """Nouveau contenu de live.json d'après l'analyse `a` d'images prises entre `start` et `end` (heure de Makkah) ; `None` si rien d'exploitable."""
    if not a["usable"] or a["level"] is None:
        return None
    clamp = lambda v: None if v is None else round(min(1, max(0.05, v)), 2)
    hm = lambda t: t.strftime("%H:%M")
    date, minute = end.strftime("%Y-%m-%d"), end.hour * 60 + end.minute
    now = {"date": date, "min": minute, "level": clamp(a["level"]), "from": hm(start), "to": hm(end), "frames": a["usable"],
           "rings": {k: clamp(v) for k, v in a["rings"].items() if v is not None}, "note": a["note"][:160], "prayer": a["prayer"], "source": "direct Quran TV"}
    day = old.get("day", {}) if old.get("day", {}).get("date") == date else {"date": date, "points": []}
    day["points"] = [p for p in day["points"] if p["min"] != minute] + [{"min": minute, "level": now["level"]}]
    return {"now": now, "day": day}


def self_check():
    t = datetime.datetime(2026, 11, 13, 13, 30)
    a = {"usable": 4, "level": 0.74, "rings": {"inner": 0.9, "middle": 0.7, "outer": None}, "note": "Mataf dense", "prayer": False}
    old = {"day": {"date": "2026-11-13", "points": [{"min": 780, "level": 0.6}]}}
    m = merge(old, a, t - datetime.timedelta(minutes=1), t)
    assert m["now"]["min"] == 810 and m["now"]["from"] == "13:29" and m["now"]["rings"] == {"inner": 0.9, "middle": 0.7} and len(m["day"]["points"]) == 2
    assert merge({"day": {"date": "2026-11-12", "points": [{"min": 1, "level": 1}]}}, a, t, t)["day"]["points"] == [{"min": 810, "level": 0.74}]  # nouveau jour : on repart de zéro
    assert merge(old, dict(a, usable=0, level=None), t, t) is None


def main():
    self_check()
    makkah = lambda: datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    dir = pathlib.Path(tempfile.mkdtemp(prefix="omra-live-"))
    try:
        for attempt in range(2):
            start = makkah()
            n = capture(dir)
            end = makkah()
            if n < 3:
                return print(end.strftime("%Y-%m-%d %H:%M"), "direct indisponible :", n, "image(s)")
            a = analyse(dir, n)
            print(end.strftime("%Y-%m-%d %H:%M"), json.dumps(a, ensure_ascii=False), flush=True)
            if a["usable"] or attempt:
                break
            time.sleep(180)  # la régie montre parfois le ciel ou la ville pendant une minute : un second essai un peu plus tard
        old = json.loads(OUT.read_text()) if OUT.exists() else {}
        new = merge(old, a, start, end)
        if new is None or "--dry" in sys.argv:
            return  # aucune image du Mataf : on garde le relevé précédent, l'app retombe d'elle-même sur l'estimation après 2 h
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n")
        pub = HERE.parent / "publish.sh"  # sur le Mac seulement ; sur GitHub, c'est releve.yml qui publie
        if pub.exists():
            subprocess.run([str(pub)], cwd=HERE.parent, timeout=120, check=True)
    finally:
        if "--keep" in sys.argv:
            print(dir)
        else:
            shutil.rmtree(dir, ignore_errors=True)


if __name__ == "__main__":
    main()
