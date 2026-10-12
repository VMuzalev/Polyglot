"""
build_data.py - собирает данные для аддона Polyglot.

Данные берутся из таблицы ItemSparse через wago.tools:
  1) клиент WoW Forever (главный источник);
  2) клиент Classic Era (запасной): часть предметов в таблицах Forever отсутствует,
     хотя в игре они есть. Названия таких предметов берутся из Classic Era.

Запуск:    python tools/build_data.py
           python tools/build_data.py --check   (только определить новейшую сборку, ничего не писать)
           python tools/build_data.py --find 7708   (диагностика: есть ли предмет в таблицах wago.tools)
           python tools/build_data.py --tables      (диагностика: колонки и примеры таблиц зон AreaTable и Map)
           python tools/build_data.py --rows UiMap UiMapAssignment Map --where AreaID=1 --where ID=36
                                                    (диагностика: все колонки таблиц и строки с указанными значениями)
Результат:
  Build.lua                           метка сборки (читается основным аддоном)
  Modules/Polyglot_<локаль>/           по одному модулю на язык (загружаются по требованию)

Если репозиторий лежит прямо в Interface\\AddOns\\Polyglot, модули дополнительно копируются
в Interface\\AddOns\\Polyglot_<локаль>, чтобы игра их увидела (как в готовом архиве).
"""
import contextlib
import csv
import io
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

# Сборки пробуются по очереди, берётся первая, которая скачалась.
# Актуальные номера: https://wago.tools/builds
FOREVER_BUILDS = ["1.60.1.70205", "1.60.1.70170"]      # продукт wow_classic_beta
CLASSIC_ERA_BUILDS = ["1.15.9.69722"]                  # продукт wow_classic_era

# Новые сборки Forever ищутся автоматически (см. discover_forever_builds), а список выше служит
# запасным вариантом. Если после запуска игры номер версии изменится (например, 1.61.x), добавьте префикс.
FOREVER_VERSION_PREFIXES = ("1.60.",)
# Где искать номера сборок: страница wago.tools и сервер версий Blizzard (продукт беты Forever).
DISCOVERY_URLS = (
    "https://wago.tools/builds",
    "https://us.version.battle.net/v2/products/wow_classic_beta/versions",
)

# Локаль WoW (она же код языка в wago.tools) -> подпись для списка аддонов.
# Список должен совпадать с move-folders в .pkgmeta и с LOCALES в Polyglot.lua.
LOCALES = {
    "enUS": "English",
    "ruRU": "Русский",
    "deDE": "Deutsch",
    "frFR": "Français",
    "esES": "Español (España)",
    "esMX": "Español (México)",
    "itIT": "Italiano",
    "ptBR": "Português (Brasil)",
    "koKR": "Korean",
    "zhCN": "Chinese (Simplified)",
    "zhTW": "Chinese (Traditional)",
}

# Без этих языков релиз не собирается. Остальные при проблемах превращаются в пустой модуль.
REQUIRED = ("enUS", "ruRU")

# Защита от испорченных данных: если английских названий меньше, скрипт остановится.
MIN_ITEMS = 10000

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MODULES_DIR = os.path.join(ROOT, "Modules")
CHUNK = 4000   # записей в одном блоке (у Lua есть лимит на число констант в функции)


# ---------------------------------------------------------------- поиск сборок

def _fetch_text(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Polyglot builder)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_forever_versions(text):
    """Достаёт из произвольного текста номера сборок Forever вида 1.60.1.70205, самые новые первыми."""
    prefixes = "|".join(re.escape(p) for p in FOREVER_VERSION_PREFIXES)
    found = set(re.findall(rf"(?<![\d.])(?:{prefixes})\d+\.\d{{5,6}}(?![\d.])", text))
    return sorted(found, key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)


def discover_forever_builds():
    """Ищет номера сборок Forever на wago.tools и сервере Blizzard. Любые сбои не критичны: вернётся []."""
    found = set()
    for url in DISCOVERY_URLS:
        try:
            versions = extract_forever_versions(_fetch_text(url))
        except Exception as e:  # сеть, формат страницы и т.д.: это только подсказка
            print(f"  Поиск сборок: {url} недоступен ({e})")
            continue
        print(f"  Поиск сборок: {url}: найдено {len(versions)}")
        found.update(versions)
    return sorted(found, key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)


def forever_build_candidates():
    """Сначала найденные автоматически (новые первыми), затем запасной список."""
    discovered = discover_forever_builds()[:3]
    return discovered + [b for b in FOREVER_BUILDS if b not in discovered]


# ---------------------------------------------------------------- загрузка

def download(build, locale, attempts=3, table="ItemSparse"):
    url = f"https://wago.tools/db2/{table}/csv?build={build}&locale={locale}"
    print("Скачиваю:", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Polyglot builder)"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode("utf-8-sig")
        except (urllib.error.URLError, TimeoutError) as e:
            not_found = isinstance(e, urllib.error.HTTPError) and e.code == 404
            if not_found or attempt == attempts:
                raise
            print(f"  попытка {attempt} не удалась ({e}), повторяю")
            time.sleep(3 * attempt)


def parse(text):
    reader = csv.DictReader(io.StringIO(text))
    cols = reader.fieldnames or []
    if "ID" not in cols or "Display_lang" not in cols:
        sys.exit("Не нашёл колонки ID / Display_lang. Первые колонки файла: " + ", ".join(cols[:15]))
    result = {}
    for row in reader:
        if row["ID"].isdigit():
            result[int(row["ID"])] = row["Display_lang"].strip()
    return result


def load_source(label, builds):
    """Пробует сборки по очереди. Возвращает ({локаль: {id: название} или None}, номер сборки)."""
    for build in builds:
        try:
            data = {"enUS": parse(download(build, "enUS"))}
        except urllib.error.URLError as e:
            print(f"  {label} {build}: не получилось ({e}), пробую следующую сборку")
            continue
        for loc in LOCALES:
            if loc == "enUS":
                continue
            try:
                data[loc] = parse(download(build, loc))
            except urllib.error.URLError as e:
                if loc in REQUIRED:
                    sys.exit(f"ОШИБКА: {label} {build}: язык {loc} не скачался ({e}).")
                print(f"  ПРЕДУПРЕЖДЕНИЕ: {label} {build}: язык {loc} не скачался ({e})")
                data[loc] = None
        print(f"  {label} {build}: {len(data['enUS'])} предметов")
        return data, build
    sys.exit(f"Не удалось скачать данные {label}. Проверьте номера сборок на wago.tools/builds")


def merge(primary, fallback):
    """Данные primary главнее, пустые названия добираются из fallback."""
    merged = {}
    for loc in LOCALES:
        p, f = primary.get(loc), fallback.get(loc)
        if p is None and f is None:
            merged[loc] = None
            continue
        d = dict(f or {})
        for item_id, name in (p or {}).items():
            if name or item_id not in d:
                d[item_id] = name
        merged[loc] = d
    return merged


def validate(merged):
    """Проверяет данные. Возвращает {локаль: {id: название} или None (модуль будет пустым)}."""
    en = merged["enUS"]
    en_count = sum(1 for n in en.values() if n)
    if en_count < MIN_ITEMS:
        sys.exit(f"ОШИБКА: английских названий {en_count}, ожидается не меньше {MIN_ITEMS}. Файлы не записаны.")

    result = {"enUS": en}
    for loc in LOCALES:
        if loc == "enUS":
            continue
        names = merged.get(loc)
        problem = None
        if not names:
            problem = "нет данных"
        else:
            filled = [(i, n) for i, n in names.items() if n]
            if len(filled) < 0.5 * en_count:
                problem = f"слишком мало названий ({len(filled)})"
            else:
                same = sum(1 for i, n in filled if n == en.get(i))
                if same / len(filled) > 0.8:
                    problem = "названия совпадают с английскими (язык не переключился)"
        if problem:
            if loc in REQUIRED:
                sys.exit(f"ОШИБКА: {loc}: {problem}. Файлы не записаны.")
            print(f"ПРЕДУПРЕЖДЕНИЕ: {loc}: {problem}, модуль будет пустым")
            result[loc] = None
        else:
            result[loc] = names
    return result


# ---------------------------------------------------------------- запись

# ---------------------------------------------------------------- места (зоны и подземелья)

# Ключ места в Lua: ID зоны из AreaTable как есть, карты (подземелья) из Map сдвинуты, чтобы не пересекаться.
MAP_KEY_BASE = 1000000
# Вид места: c континент, z зона, s подзона, d подземелье, r рейд, b поле боя, a арена.
INSTANCE_KINDS = {"0": "c", "1": "d", "2": "r", "3": "b", "4": "a"}
# Служебные записи клиента (тестовые, неиспользуемые), которые не нужны игрокам.
JUNK_NAME = re.compile(
    r"^(test\b|unused|zz|<|\[|\(|dev\b|debug|placeholder|temp\b|old\b)"      # служебное в начале названия
    r"|\b(not used|unused|do not use|obsolete|deprecated|placeholder)\b"     # или где угодно («Not Used Deadmines»)
    r"|\((test|old|obsolete|unused)\)",                                       # пометка в скобках
    re.IGNORECASE)
JUNK_MAP_DIR = ("test", "development", "transport")


def _table_rows(text, name_col):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "ID" not in rows[0] or name_col not in rows[0]:
        raise ValueError(f"нет колонок ID / {name_col}")
    return rows


def parse_places(area_text, map_text):
    """Возвращает ({ключ: вид}, {ключ: название}) по CSV таблиц AreaTable и Map одного языка."""
    kinds, names = {}, {}
    for row in _table_rows(area_text, "AreaName_lang"):
        name = row["AreaName_lang"].strip()
        if not row["ID"].isdigit() or not name or JUNK_NAME.search(name):
            continue
        parent = row.get("ParentAreaID", "0")
        key = int(row["ID"])
        kinds[key] = "s" if parent not in ("", "0") else "z"
        names[key] = name
    for row in _table_rows(map_text, "MapName_lang"):
        name = row["MapName_lang"].strip()
        directory = row.get("Directory", "").lower()
        if (not row["ID"].isdigit() or not name or JUNK_NAME.search(name)
                or any(bad in directory for bad in JUNK_MAP_DIR)):
            continue
        kind = INSTANCE_KINDS.get(row.get("InstanceType", ""))
        if kind is None:
            continue
        key = MAP_KEY_BASE + int(row["ID"])
        kinds[key] = kind
        names[key] = name
    return kinds, names


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_place_maps(area_text, map_text, uimap_text, assign_text, kinds):
    """Карта для каждого места: {ключ: [uiMapID] или [uiMapID, x, y]} (x, y: доли 0..1 от левого верхнего угла).
    Зоны и подзоны открывают карту своей зоны (подзона: родительской), континенты свою карту,
    подземелья карту зоны (или континента), где находится вход, плюс координаты входа.
    Координаты входа это Corpse_0 (север-юг) и Corpse_1 (восток-запад) из таблицы Map."""
    ui_type = {}
    for r in csv.DictReader(io.StringIO(uimap_text)):
        if r.get("ID", "").isdigit():
            ui_type[int(r["ID"])] = r.get("Type", "")
    zone_by_area, continent_by_map, zone_rects = {}, {}, []
    for r in csv.DictReader(io.StringIO(assign_text)):
        if not (r.get("UiMapID", "").isdigit() and r.get("MapID", "").lstrip("-").isdigit()):
            continue
        ui, map_id, area = int(r["UiMapID"]), int(r["MapID"]), int(_num(r.get("AreaID")))
        xs = sorted((_num(r.get("Region_0")), _num(r.get("Region_3"))))
        ys = sorted((_num(r.get("Region_1")), _num(r.get("Region_4"))))
        rect = (xs[0], xs[1], ys[0], ys[1])
        if rect[0] == rect[1] or rect[2] == rect[3]:
            continue
        if ui_type.get(ui) == "3" and area:
            zone_by_area.setdefault(area, ui)
            zone_rects.append((map_id, ui, rect))
        elif ui_type.get(ui) == "2" and not area:
            continent_by_map.setdefault(map_id, (ui, rect))

    def place_on(rect, x, y):
        """Доли карты для точки мира (x север, y запад). None, если точка вне прямоугольника."""
        xmin, xmax, ymin, ymax = rect
        if not (xmin <= x <= xmax and ymin <= y <= ymax):
            return None
        return round((ymax - y) / (ymax - ymin), 4), round((xmax - x) / (xmax - xmin), 4)

    parent = {}
    for r in csv.DictReader(io.StringIO(area_text)):
        if r.get("ID", "").isdigit():
            parent[int(r["ID"])] = int(_num(r.get("ParentAreaID")))
    maps = {}
    for key, kind in kinds.items():
        if key < MAP_KEY_BASE:
            area = key
            for _ in range(10):            # подзона берёт карту родительской зоны
                if area in zone_by_area or not parent.get(area):
                    break
                area = parent[area]
            if area in zone_by_area:
                maps[key] = [zone_by_area[area]]
    for r in csv.DictReader(io.StringIO(map_text)):
        if not r.get("ID", "").isdigit():
            continue
        key = MAP_KEY_BASE + int(r["ID"])
        kind = kinds.get(key)
        if kind == "c":
            if int(r["ID"]) in continent_by_map:
                maps[key] = [continent_by_map[int(r["ID"])][0]]
        elif kind:
            corpse_map = int(_num(r.get("CorpseMapID"), -1))
            x, y = _num(r.get("Corpse_0")), _num(r.get("Corpse_1"))
            if corpse_map < 0 or (x == 0 and y == 0):
                continue                    # входа в данных нет
            best = None
            for map_id, ui, rect in zone_rects:
                pos = place_on(rect, x, y) if map_id == corpse_map else None
                size = (rect[1] - rect[0]) * (rect[3] - rect[2])
                if pos and (best is None or size < best[0]):
                    best = (size, ui, pos)
            if best is None and corpse_map in continent_by_map:
                ui, rect = continent_by_map[corpse_map]
                pos = place_on(rect, x, y)
                best = (0, ui, pos) if pos else None
            if best:
                maps[key] = [best[1], best[2][0], best[2][1]]
    return maps


def load_places(build, locales):
    """Скачивает зоны и подземелья. Возвращает ({ключ: вид}, {локаль: {ключ: название}}).
    Места необязательны: при любой проблеме с английским языком возвращается пустой результат."""
    kinds, by_locale, en_texts = {}, {}, None
    for loc in locales:
        try:
            area_text, map_text = download(build, loc, table="AreaTable"), download(build, loc, table="Map")
            k, n = parse_places(area_text, map_text)
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            print(f"  места {loc}: нет данных ({e})")
            continue
        if loc == "enUS":
            kinds = k
            en_texts = (area_text, map_text)
        by_locale[loc] = n
    if not kinds:
        print("ВНИМАНИЕ: английские названия мест не получены, поиск по местам работать не будет")
        return {}, {}, {}
    maps = {}
    try:
        maps = parse_place_maps(*en_texts, download(build, "enUS", table="UiMap"),
                                download(build, "enUS", table="UiMapAssignment"), kinds)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        print(f"ВНИМАНИЕ: данные карт не получены ({e}), клик по месту не откроет карту")
    # Одинаковые места под разными ID (одно и то же название на всех языках) оставляем один раз
    seen, drop = {}, set()
    for key in sorted(kinds):
        sig = (kinds[key],) + tuple(by_locale.get(loc, {}).get(key, "") for loc in locales)
        if sig in seen:
            drop.add(key)
        else:
            seen[sig] = key
    kinds = {k: v for k, v in kinds.items() if k not in drop}
    by_locale = {loc: {k: n for k, n in d.items() if k in kinds} for loc, d in by_locale.items()}
    maps = {k: v for k, v in maps.items() if k in kinds}
    return kinds, by_locale, maps


def lua_str(s):
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", " ")
    return '"' + s + '"'


def read_interface():
    """Номер Interface берётся из основного Polyglot.toc, чтобы модули всегда совпадали с ним."""
    try:
        with open(os.path.join(ROOT, "Polyglot.toc"), encoding="utf-8-sig") as f:
            for line in f:
                if line.lower().startswith("## interface:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "16001"


def write_module(loc, label, names, interface, places=None, kinds=None, maps=None):
    folder = os.path.join(MODULES_DIR, f"Polyglot_{loc}")
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, f"Polyglot_{loc}.toc"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"## Interface: {interface}\n")
        f.write(f"## Title: Polyglot: {label} ({loc})\n")
        f.write(f"## Notes: Названия предметов для Polyglot, язык {loc}. Загружается по требованию.\n")
        f.write("## LoadOnDemand: 1\n")
        f.write("## Dependencies: Polyglot\n")
        f.write("## Category: Localization\n")        # раздел в списке аддонов (11.1.0+)
        f.write("## Category-ruRU: Локализация\n")
        f.write("## Group: Polyglot\n")   # в списке аддонов модули сворачиваются под основным (клиент 11.1.0+)
        f.write("## IconTexture: Interface\\Icons\\INV_Misc_Book_04\n")   # иконка в списке аддонов
        f.write("## Version: @project-version@\n")
        f.write("Data.lua\n")

    with open(os.path.join(folder, "Data.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write("PolyglotData = PolyglotData or {}\n")
        if not names:
            f.write(f'PolyglotData["{loc}"] = {{ n = {{}}, l = {{}}, p = {{}}, pl = {{}}, k = {{}}, mp = {{}}, empty = true }}\n')
            return 0
        ids = sorted(i for i, n in names.items() if n)
        f.write("local n, l = {}, {}\n")
        f.write("local function run(fn) fn() end\n")
        for start in range(0, len(ids), CHUNK):
            f.write("run(function()\n")
            for item_id in ids[start:start + CHUNK]:
                name = names[item_id]
                f.write(f"n[{item_id}]={lua_str(name)}\nl[{item_id}]={lua_str(name.lower())}\n")
            f.write("end)\n")
        # места: p названия, pl в нижнем регистре, k виды (только в английском модуле)
        f.write("local p, pl, k, mp = {}, {}, {}, {}\n")
        keys = sorted(places or {})
        for start in range(0, len(keys), CHUNK):
            f.write("run(function()\n")
            for key in keys[start:start + CHUNK]:
                name = places[key]
                f.write(f"p[{key}]={lua_str(name)}\npl[{key}]={lua_str(name.lower())}\n")
                if kinds and key in kinds:
                    f.write(f'k[{key}]="{kinds[key]}"\n')
                if maps and key in maps:
                    f.write(f"mp[{key}]={{{','.join(str(v) for v in maps[key])}}}\n")
            f.write("end)\n")
        f.write(f'PolyglotData["{loc}"] = {{ n = n, l = l, p = p, pl = pl, k = k, mp = mp }}\n')
        return len(ids)


def copy_to_addons(locales):
    """Для разработки: если репозиторий лежит в Interface\\AddOns, копируем модули рядом."""
    addons = os.path.dirname(ROOT)
    if os.path.basename(addons).lower() != "addons":
        return
    for loc in locales:
        src = os.path.join(MODULES_DIR, f"Polyglot_{loc}")
        dst = os.path.join(addons, f"Polyglot_{loc}")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    print(f"Модули скопированы в {addons}")


def find_item(item_id):
    """Диагностика: показывает, есть ли предмет в таблицах Forever и Classic Era и под какими названиями.
    Проверяются ItemSparse (то, что мы используем) и ItemSearchName (возможный запасной источник)."""
    sources = (("Forever", forever_build_candidates()), ("Classic Era", CLASSIC_ERA_BUILDS))
    tables = ("ItemSparse", "ItemSearchName")
    print(f"Ищу предмет {item_id} в таблицах wago.tools...")
    for label, candidates in sources:
        build = None
        for candidate in candidates:                       # первая сборка, для которой таблица скачивается
            try:
                download(candidate, "enUS")
                build = candidate
                break
            except urllib.error.URLError:
                continue
        if build is None:
            print(f"  {label}: ни одна сборка не скачалась")
            continue
        for table in tables:
            parts = []
            for loc in ("enUS", "ruRU"):
                try:
                    reader = csv.DictReader(io.StringIO(download(build, loc, table=table)))
                    cols = reader.fieldnames or []
                    if "ID" not in cols or "Display_lang" not in cols:
                        parts.append(f"{loc}: нет колонок ID/Display_lang (есть: {', '.join(cols[:8])})")
                        continue
                    total, found = 0, None
                    for row in reader:
                        total += 1
                        if row["ID"] == str(item_id):
                            found = row["Display_lang"]
                    parts.append(f"{loc}: " + (f"«{found}»" if found else "нет записи") + f" (строк в таблице: {total})")
                except urllib.error.URLError as e:
                    parts.append(f"{loc}: таблица недоступна ({e})")
            print(f"  {label} {build} | {table}: " + "; ".join(parts))


def inspect_tables(tables=("AreaTable", "Map"), locales=("enUS", "ruRU"), samples=4):
    """Диагностика для будущего поиска зон и подземелий: что реально лежит в таблицах wago.tools.
    Ничего не записывает. Печатает колонки, число строк и несколько примеров на каждом языке."""
    for build in forever_build_candidates():
        reachable = False
        for table in tables:
            print(f"=== {table} | сборка Forever {build}")
            rows = {}
            for loc in locales:
                try:
                    text = download(build, loc, table=table)
                except urllib.error.URLError as e:
                    print(f"  {loc}: таблица недоступна ({e})")
                    continue
                reachable = True
                reader = csv.DictReader(io.StringIO(text))
                rows[loc] = list(reader)
                print(f"  {loc}: строк {len(rows[loc])}")
                if loc == locales[0]:
                    print("  колонки:", ", ".join(reader.fieldnames or []))
            if len(rows) == len(locales):
                first, second = (rows[l] for l in locales[:2])
                name_cols = [c for c in (first[0].keys() if first else []) if c.endswith("_lang")]
                for col in name_cols:
                    diff = sum(1 for a, b in zip(first, second) if a.get(col) and a.get(col) != b.get(col))
                    print(f"  {col}: отличается между {locales[0]} и {locales[1]} в {diff} строках")
                for a, b in list(zip(first, second))[:samples]:
                    print("  пример:", {k: v for k, v in list(a.items())[:6]}, "->", {c: b.get(c) for c in name_cols})
        if reachable:
            return
    sys.exit("Не удалось скачать таблицы ни для одной сборки")


def inspect_rows(tables, where, samples=3):
    """Диагностика: печатает все колонки таблиц (английский язык) и строки, где колонка равна значению.
    where: список строк вида КОЛОНКА=ЗНАЧЕНИЕ. Таблицы без такой колонки пропускаются для этого условия."""
    conditions = [w.split("=", 1) for w in where if "=" in w]
    for build in forever_build_candidates():
        reachable = False
        for table in tables:
            print(f"=== {table} | сборка Forever {build}")
            try:
                text = download(build, "enUS", table=table)
            except urllib.error.URLError as e:
                print(f"  таблица недоступна ({e})")
                continue
            reachable = True
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            print(f"  строк: {len(rows)}")
            print("  колонки:", ", ".join(reader.fieldnames or []))
            for row in rows[:samples]:
                print("  пример:", dict(row))
            for col, value in conditions:
                found = [r for r in rows if r.get(col) == value][:5]
                if found or col in (reader.fieldnames or []):
                    print(f"  {col}={value}: найдено {len(found)}")
                for row in found:
                    print("   ", dict(row))
        if reachable:
            return
    sys.exit("Не удалось скачать таблицы ни для одной сборки")


def check_latest_build():
    """Определяет самую новую сборку Forever, для которой на wago.tools есть таблица предметов.
    Скачивает только один небольшой файл. Ничего не записывает. Печатает номер сборки."""
    with contextlib.redirect_stdout(sys.stderr):   # журнал в stderr, чтобы stdout содержал только номер
        for build in forever_build_candidates():
            try:
                parse(download(build, "enUS"))
            except urllib.error.URLError as e:
                print(f"  {build}: не получилось ({e}), пробую следующую")
                continue
            print(f"  Новейшая доступная сборка: {build}")
            break
        else:
            sys.exit("Не удалось определить сборку Forever")
    print(build)


def main():
    if "--check" in sys.argv:
        check_latest_build()
        return
    if "--rows" in sys.argv:
        args = sys.argv[sys.argv.index("--rows") + 1:]
        names, where, i = [], [], 0
        while i < len(args):
            if args[i] == "--where" and i + 1 < len(args):
                where.append(args[i + 1])
                i += 2
            else:
                names.append(args[i])
                i += 1
        if not names:
            sys.exit("Использование: python tools/build_data.py --rows <Таблица>... [--where КОЛОНКА=ЗНАЧЕНИЕ]...")
        inspect_rows(names, where)
        return
    if "--tables" in sys.argv:
        inspect_tables()
        return
    if "--find" in sys.argv:
        try:
            find_item(int(sys.argv[sys.argv.index("--find") + 1]))
        except (IndexError, ValueError):
            sys.exit("Использование: python tools/build_data.py --find <ID предмета>")
        return
    forever, forever_build = load_source("Forever", forever_build_candidates())
    era, _ = load_source("Classic Era", CLASSIC_ERA_BUILDS)
    names_by_locale = validate(merge(forever, era))

    only_era = len(set(era["enUS"]) - set(forever["enUS"]))
    print(f"Из Classic Era добавлено предметов, которых нет в Forever: {only_era}")

    # Всё проверено, теперь можно писать файлы
    shutil.rmtree(MODULES_DIR, ignore_errors=True)
    interface = read_interface()
    place_kinds, place_names, place_maps = load_places(forever_build, list(LOCALES))
    print(f"Мест (зоны, подземелья): {len(place_kinds)}, с картой: {len(place_maps)}")
    for loc, label in LOCALES.items():
        count = write_module(loc, label, names_by_locale[loc], interface,
                             places=place_names.get(loc), kinds=place_kinds if loc == "enUS" else None,
                             maps=place_maps if loc == "enUS" else None)
        print(f"  {loc}: {count if count else 'пустой модуль'}")

    with open(os.path.join(ROOT, "Build.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"PolyglotDataBuild = {lua_str(forever_build)}\n")

    copy_to_addons(LOCALES)
    print(f"Готово: сборка Forever {forever_build}, модулей: {len(LOCALES)}")


if __name__ == "__main__":
    main()
