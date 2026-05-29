import re
import sys
import json
import argparse

import pdfplumber

# zodiac signs encoded as letters a-l
SIGNS = {
    "a": "Aries", "b": "Taurus", "c": "Gemini", "d": "Cancer",
    "e": "Leo", "f": "Virgo", "g": "Libra", "h": "Scorpio",
    "i": "Sagittarius", "j": "Capricorn", "k": "Aquarius", "l": "Pisces",
}

# single-letters used for each body mapped to their names
BODY_NAMES = {
    "A": "Sun", "B": "Moon", "C": "Mercury", "D": "Venus", "E": "Mars",
    "F": "Jupiter", "G": "Saturn", "O": "Uranus", "I": "Neptune",
    "J": "Pluto", "K": "Mean Node", "L": "True Node", "N": "Chiron",
    "Q": "Q", "T": "T",  # extra rows that appear in the aspect grid
}

# aspect glyphs in the grid encoded as letters!
# derived this empirically by cross-referencing the written orbs against the true angular
# distances computed from the longitudes (also in this sheet)
# ... this is to say, every entry except 'w' is confirmed against measured aspects:
#   m=conjunction n=opposition o=square p=trine q=sextile r=semisextile
#   s=quincunx t=semisquare u=sesquiquadrate v=quintile
ASPECT_GLYPHS = {
    "m": "conjunction", "n": "opposition", "o": "square", "p": "trine",
    "q": "sextile", "r": "semisextile", "s": "quincunx", "t": "semisquare",
    "u": "sesquiquadrate", "v": "quintile", "w": "biquintile",
}

# ideal angle (degrees) for each aspect ... used to verify orbs & to label the 
# angular target of each aspect in the output
ASPECT_ANGLES = {
    "conjunction": 0, "semisextile": 30, "semisquare": 45, "sextile": 60,
    "quintile": 72, "square": 90, "trine": 120, "sesquiquadrate": 135,
    "biquintile": 144, "quincunx": 150, "opposition": 180,
}

def dms_to_decimal(deg, minute, sec):
    """convert degrees/minutes/seconds into a single decimal number"""
    return round(deg + minute / 60 + (sec or 0) / 3600, 6)


def parse_metadata(text):
    """pull header block: name, birth date, place, and various times"""
    meta = {}
    # name is on its own line near top, before "born on"
    m = re.search(r"\n([A-Z][A-Za-z\-']+)\s+Time", text)
    if m:
        meta["name"] = m.group(1)
    m = re.search(r"born on\s+(.+?)\s+Univ\.Time", text)
    if m:
        meta["birth_date"] = m.group(1).strip()
    m = re.search(r"\nin\s+(.+?),\s+(\d+[ew]\d+),\s+(\d+[ns]\d+)", text)
    if m:
        meta["birth_place"] = m.group(1).strip()
        meta["longitude"] = m.group(2)
        meta["latitude"] = m.group(3)
    for label, key in [("Time", "local_time"), ("Univ.Time", "universal_time"),
                       ("Sid. Time", "sidereal_time")]:
        m = re.search(re.escape(label) + r"\s+([0-9:]+(?:\s*[ap]\.m\.)?)", text)
        if m:
            meta[key] = m.group(1).strip()
    m = re.search(r"Jul\.Day\s+([\d.]+)", text)
    if m:
        meta["julian_day"] = m.group(1)
    return meta


def parse_planets(text):
    """
    each planet occupies two lines in extracted text ... first line has
    the body code + sign glyph + (house sign glyph); the second has the actual numbers.
    """
    planets = []
    lines = text.split("\n")

    # line shape:  Sun 28°40'15" 3 57'48" 0° 0' 0" N 11°56'13" N 1 0° 0' 0" ...
    # to keep alignment of numbers, anchor on body name
    name_to_code = {v: k for k, v in BODY_NAMES.items()}

    for i, line in enumerate(lines):
        for name in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter",
                     "Saturn", "Uranus", "Neptune", "Pluto", "Mean Node",
                     "True Node", "Chiron"):
            if not line.lstrip().startswith(name + " "):
                continue
            # previous line holds the sign glyph + retrograde flag for current body
            prev = lines[i - 1] if i > 0 else ""
            glyph_match = re.search(r"\b([A-Z])\s+([a-l])\s*(#?)", prev)
            sign = SIGNS.get(glyph_match.group(2)) if glyph_match else None
            retrograde = bool(glyph_match and glyph_match.group(3) == "#")

            # longitude (deg°min'sec") and the house # following it
            lon = re.search(
                r"(\d+)°\s*(\d+)'\s*(\d+)\"\s+(\d+)", line)
            # latitude and declination: "...N 11°56'13" N"
            decl = re.findall(r"(\d+)°\s*(\d+)'\s*(\d+)\"\s*([NS])", line)

            entry = {
                "body": name,
                "code": name_to_code.get(name),
                "sign": sign,
                "retrograde": retrograde,
            }
            if lon:
                d, mi, se, house = lon.groups()
                entry["longitude_dms"] = f"{d}\u00b0{mi}'{se}\""
                entry["degree_in_sign"] = dms_to_decimal(int(d), int(mi), int(se))
                entry["house"] = int(house)
            # last two N/S-tagged values on the line are latitude & declination
            if len(decl) >= 2:
                lat_d, lat_m, lat_s, lat_h = decl[-2]
                dec_d, dec_m, dec_s, dec_h = decl[-1]
                entry["latitude"] = f"{lat_d}\u00b0{lat_m}'{lat_s}\" {lat_h}"
                entry["declination"] = f"{dec_d}\u00b0{dec_m}'{dec_s}\" {dec_h}"
            planets.append(entry)
            break
    return planets


def parse_houses(text):
    """
    house cusps are in right-hand column, appended to each planet line as
    "... <house#> <deg>° <min>' <sec>" <decl> N". Whole-sign cusps are all at
    0°0'0", and the cusp's sign glyph is the SECOND glyph on the prior line
    (first glyph for planet, second for house column)
    """
    houses = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        # match the trailing house block: a 1-2 digit house number followed by dms cusp value
        # take LAST such match on the line
        matches = re.findall(r"\b(\d{1,2})\s+(\d+)°\s*(\d+)'\s*(\d+)\"\s+\d+°", line)
        if not matches:
            continue
        hn, d, mi, se = matches[-1]
        if not (1 <= int(hn) <= 12):
            continue
        # house-column sign glyph is the 2nd independent a-l letter on prior line (first is planet's sign)
        prev = lines[i - 1] if i > 0 else ""
        glyphs = re.findall(r"(?<![A-Za-z])([a-l])(?![A-Za-z])", prev)
        cusp_sign = SIGNS.get(glyphs[1]) if len(glyphs) >= 2 else None
        houses.append({
            "house": int(hn),
            "sign": cusp_sign,
            "cusp_dms": f"{d}\u00b0{mi}'{se}\"",
        })
    houses.sort(key=lambda h: h["house"])
    # ac + mc
    points = {}
    m = re.search(r"Asc\.\s+(\d+)°\s*(\d+)'\s*(\d+)\"", text)
    if m:
        points["Ascendant"] = f"{m.group(1)}\u00b0{m.group(2)}'{m.group(3)}\""
    m = re.search(r"MC\s+(\d+)°\s*(\d+)'\s*(\d+)\"", text)
    if m:
        points["Midheaven"] = f"{m.group(1)}\u00b0{m.group(2)}'{m.group(3)}\""
    return houses, points

# order in which bodies appear as both rows and columns of the aspect grid.
# ... columns for a given row are the bodies that come BEFORE it in this order, such that 
# the glyphs in a row are l-to-r against only those earlier bodies that an aspect 
# actually exists with (bodies out of orb are skipped w/out placeholder)
ASPECT_GRID_ORDER = ["A", "B", "C", "D", "E", "F", "G", "O", "I", "J",
                     "K", "L", "N", "Q", "T"]

def parse_aspects(text):
    """
    parse the lower-triangular aspect grid. (yay for lower-triangular matrices & my linear algebra talent)

    each aspect occupies two lines: a header line of the form:  "<ROW> g g g ... <ROW>"   (glyph per aspected earlier body)
    , followed by an orb line:  "<orb> <orb> <orb> ..."   (one orb per glyph, same order)
    ... orbs look like  -8°05a  (deg°min + a/s)

    map each glyph to an earlier body by tracing ASPECT_GRID_ORDER and extracting l-to-r
    """
    aspects = []
    lines = text.split("\n")

    # locate "Aspects" header so we only parse that which is below it
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "Aspects")
    except StopIteration:
        return aspects

    i = start + 1
    orb_re = re.compile(r"-?\d+°\s*\d+[as]")
    while i < len(lines):
        line = lines[i].strip()
        # a header row starts and ends with the same body code and has glyphs
        # (lowercase a-w) between, like: "O n p n m n p O".
        hm = re.match(r"^([A-Z])\s+([a-w\s]+?)\s+\1$", line)
        if hm:
            row_code = hm.group(1)
            glyphs = hm.group(2).split()
            # this following line stores the orbs
            orbs = []
            if i + 1 < len(lines):
                orbs = orb_re.findall(lines[i + 1])
            # map glyphs to earlier bodies l-to-r
            if row_code in ASPECT_GRID_ORDER:
                priors = ASPECT_GRID_ORDER[:ASPECT_GRID_ORDER.index(row_code)]
            else:
                priors = []
            # only as many priors as aspected glyphs... priors skipped are encoded by position
            for k, g in enumerate(glyphs):
                orb = orbs[k] if k < len(orbs) else None
                aspect_name = ASPECT_GLYPHS.get(g)
                entry = {
                    "body1": BODY_NAMES.get(row_code, row_code),
                    "aspect": aspect_name,
                    "glyph": g,
                    "target_angle": ASPECT_ANGLES.get(aspect_name),
                }
                if orb is not None:
                    om = re.match(r"(-?\d+)°\s*(\d+)([as])", orb)
                    if om:
                        deg, mn, mode = om.groups()
                        sign = -1 if deg.startswith("-") else 1
                        entry["orb_degrees"] = round(
                            sign * (abs(int(deg)) + int(mn) / 60), 4)
                        entry["motion"] = ("applying" if mode == "a"
                                           else "separating")
                aspects.append(entry)
            i += 2
            continue
        i += 1
    return aspects


def resolve_aspect_bodies(aspects, planets):
    """
    resolve body2 for each aspect by matching printed orb to the true
    angular distance between the row body and each candidate earlier body.

    only works for bodies whose longitude is on the sheet (the ten planets plus
    True Node, Chiron). 
    extraneous grid points (Q, T) and Mean Node lack a printed
    longitude, so those aspects keep body2 = None and are flagged.
    """
    # get absolute longitudes from planet table
    sign_order = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
                  "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius",
                  "Pisces"]
    lon = {}
    for p in planets:
        if p.get("sign") and p.get("degree_in_sign") is not None:
            lon[p["body"]] = sign_order.index(p["sign"]) * 30 + p["degree_in_sign"]

    def separation(a, b):
        x = abs(lon[a] - lon[b]) % 360
        return min(x, 360 - x)

    # group aspects by row body, in grid order, and extract priors l-to-r while matching orbs
    # .. allows recovery of skipped bodies
    from collections import defaultdict
    by_body = defaultdict(list)
    for a in aspects:
        by_body[a["body1"]].append(a)

    code_for = {v: k for k, v in BODY_NAMES.items()}
    for body1, group in by_body.items():
        code1 = code_for.get(body1)
        if code1 is None or body1 not in lon:
            continue
        prior_codes = ASPECT_GRID_ORDER[:ASPECT_GRID_ORDER.index(code1)]
        prior_bodies = [BODY_NAMES.get(c) for c in prior_codes
                        if BODY_NAMES.get(c) in lon]
        used = set()
        for a in group:
            if a.get("orb_degrees") is None or a["target_angle"] is None:
                continue
            target = a["target_angle"]
            want = abs(a["orb_degrees"])
            best, best_err = None, 0.2  # 0.2° tolerance
            for b in prior_bodies:
                if b in used:
                    continue
                err = abs(abs(separation(body1, b) - target) - want)
                if err < best_err:
                    best, best_err = b, err
            if best:
                used.add(best)
                a["body2"] = best
                a["verified"] = True
    return aspects


def scrape(path):
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    houses, points = parse_houses(text)
    planets = parse_planets(text)
    aspects = parse_aspects(text)
    aspects = resolve_aspect_bodies(aspects, planets)
    return {
        "metadata": parse_metadata(text),
        "planets": planets,
        "houses": houses,
        "points": points,
        "aspects": aspects,
    }


def main():
    ap = argparse.ArgumentParser(description="Scrape an astro.com natal data sheet PDF.")
    ap.add_argument("pdf", help="path to the Astrodienst PDF")
    ap.add_argument("--json", help="write structured output to this JSON file")
    args = ap.parse_args()

    data = scrape(args.pdf)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.json}")
    else:
        m = data["metadata"]
        print(f"{m.get('name','?')}  |  born {m.get('birth_date','?')}  in {m.get('birth_place','?')}")
        print("-" * 60)
        for p in data["planets"]:
            retro = " R" if p["retrograde"] else ""
            print(f"{p['body']:<11} {p.get('degree_in_sign','?'):>9}\u00b0 {str(p.get('sign','?')):<12}"
                  f" house {p.get('house','?'):<3}{retro}")
        print("-" * 60)
        print(f"Ascendant: {data['points'].get('Ascendant','?')}   "
              f"Midheaven: {data['points'].get('Midheaven','?')}")
        print("-" * 60)
        print("Aspects:")
        for a in data["aspects"]:
            b2 = a.get("body2") or "(extra point)"
            orb = a.get("orb_degrees")
            orb_str = f"{orb:+.2f}\u00b0" if orb is not None else "  ?  "
            motion = a.get("motion", "")[:3]
            mark = "" if a.get("verified") else "  [unverified body]"
            print(f"  {a['body1']:<10} {str(a['aspect'] or '?'):<14} "
                  f"{b2:<12} orb {orb_str} {motion}{mark}")


if __name__ == "__main__":
    main()