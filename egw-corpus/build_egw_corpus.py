"""
build_egw_corpus.py — EGW ePub → JSON corpus builder.

Run:    python3 build_egw_corpus.py <input-folder> <output-folder>
"""
import sys, os, json, re, zipfile
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree as ET


BOOK_TITLES = {
    "PP":   "Patriarchs and Prophets",
    "PK":   "Prophets and Kings",
    "DA":   "The Desire of Ages",
    "AA":   "The Acts of the Apostles",
    "GC":   "The Great Controversy",
    "COL":  "Christ's Object Lessons",
    "FLB":  "The Faith I Live By",
    "MB":   "Thoughts from the Mount of Blessing",
    "LDE":  "Last Day Events",
    "1BC":  "EGW Bible Commentary - Genesis to Deuteronomy",
    "2BC":  "EGW Bible Commentary - Joshua to 2 Kings",
    "3BC":  "EGW Bible Commentary - 1 Chronicles to Song of Solomon",
    "4BC":  "EGW Bible Commentary - Isaiah to Malachi",
    "5BC":  "EGW Bible Commentary - Matthew to John",
    "6BC":  "EGW Bible Commentary - Acts to Ephesians",
    "7BC":  "EGW Bible Commentary - Philippians to Revelation",
    "7ABC": "EGW Bible Commentary - Supplement",
}


class EGWParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs = []
        self.chapter_title = ""
        self.current_page = ""
        self._in_p = False
        self._in_chapterhead = False
        self._text_buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        # Page-break marker
        if "pagebreak" in cls or a.get("epub:type") == "pagebreak":
            title = a.get("title", "")
            if title:
                self.current_page = title
            return
        if tag == "p":
            self._in_p = True
            self._text_buf = []
        elif tag in ("h1", "h2", "h3", "h4") and "chapterhead" in cls:
            self._in_chapterhead = True
            self._text_buf = []

    def handle_endtag(self, tag):
        if tag == "p" and self._in_p:
            text = "".join(self._text_buf).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                self.paragraphs.append({
                    "page": self.current_page,
                    "chapter_title": self.chapter_title,
                    "text": text,
                })
            self._in_p = False
            self._text_buf = []
        elif tag in ("h1", "h2", "h3", "h4") and self._in_chapterhead:
            self.chapter_title = re.sub(r"\s+", " ", "".join(self._text_buf).strip())
            self._in_chapterhead = False
            self._text_buf = []

    def handle_data(self, data):
        if self._in_p or self._in_chapterhead:
            self._text_buf.append(data)


def get_spine(z):
    container = z.read("META-INF/container.xml").decode("utf-8")
    m = re.search(r'full-path="([^"]+)"', container)
    if not m:
        return []
    opf_path = m.group(1)
    opf_dir = os.path.dirname(opf_path)
    opf = z.read(opf_path).decode("utf-8")
    # Build manifest (id → href) via regex; safer than XML parsing for varied namespaces
    manifest = {}
    for item_m in re.finditer(r"<item\b([^>]*?)/?>", opf):
        attrs = item_m.group(1)
        idm = re.search(r'\bid="([^"]+)"', attrs)
        hrefm = re.search(r'\bhref="([^"]+)"', attrs)
        if idm and hrefm:
            manifest[idm.group(1)] = hrefm.group(1)
    # Spine order
    files = []
    spine_match = re.search(r"<spine\b[^>]*>(.*?)</spine>", opf, flags=re.S)
    if spine_match:
        for ir in re.finditer(r'<itemref\b[^/>]*?idref="([^"]+)"', spine_match.group(1)):
            idref = ir.group(1)
            if idref in manifest:
                p = manifest[idref]
                full = (opf_dir + "/" + p) if opf_dir else p
                files.append(full)
    return files


def parse_epub(epub_path):
    paras = []
    with zipfile.ZipFile(epub_path, "r") as z:
        spine = get_spine(z)
        for chapter_file in spine:
            try:
                html = z.read(chapter_file).decode("utf-8", errors="replace")
            except KeyError:
                continue
            p = EGWParser()
            try:
                p.feed(html)
            except Exception:
                continue
            paras.extend(p.paragraphs)
    return paras


def extract_book_code(filename):
    # Handle: en_PP.epub, en_AA (1).epub, PP_xxx.epub, etc.
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r"\s*\(\d+\)\s*$", "", name)  # strip "(1)" suffix
    if name.lower().startswith("en_"):
        return name[3:].upper()
    if "_" in name:
        return name.split("_", 1)[0].upper()
    return name.upper()


def slugify(book_code):
    title = BOOK_TITLES.get(book_code, book_code)
    s = title.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s


def build(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    epubs = sorted(Path(input_dir).glob("*.epub"))
    print(f"Found {len(epubs)} ePub(s).\n")
    summary = []
    for ep in epubs:
        code = extract_book_code(str(ep))
        title = BOOK_TITLES.get(code, code)
        print(f"  Parsing {ep.name}  →  {code}: {title}")
        raw = parse_epub(str(ep))
        out = {
            "book_code": code,
            "book_title": title,
            "total_paragraphs": len(raw),
            "paragraphs": [
                {
                    "id": f"{code}-{i:04d}",
                    "book_code": code,
                    "chapter_title": p["chapter_title"],
                    "page": p["page"],
                    "text": p["text"],
                }
                for i, p in enumerate(raw, start=1)
            ],
        }
        outfile = os.path.join(output_dir, f"{slugify(code)}.json")
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        size_mb = os.path.getsize(outfile) / 1024 / 1024
        print(f"    {len(raw):,} paragraphs → {os.path.basename(outfile)} ({size_mb:.1f} MB)")
        summary.append((code, title, len(raw), size_mb, outfile))
    return summary


if __name__ == "__main__":
    args = sys.argv[1:]
    input_dir = args[0] if len(args) >= 1 else "."
    output_dir = args[1] if len(args) >= 2 else "."
    build(input_dir, output_dir)
