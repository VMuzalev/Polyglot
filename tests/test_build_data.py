import contextlib
import io
import os
import unittest
import urllib.error

import helpers
from helpers import build_data as b, build_workspace, fake_download, lua51, read, run_build


class Merge(unittest.TestCase):
    def test_primary_wins_and_fallback_fills_gaps(self):
        primary = {loc: None for loc in b.LOCALES}
        fallback = {loc: None for loc in b.LOCALES}
        primary["ruRU"] = {1: "Новое", 2: ""}
        fallback["ruRU"] = {1: "Старое", 2: "Из запасного", 3: "Только в запасном"}
        merged = b.merge(primary, fallback)["ruRU"]
        self.assertEqual(merged, {1: "Новое", 2: "Из запасного", 3: "Только в запасном"})

    def test_locale_missing_everywhere_stays_none(self):
        none = {loc: None for loc in b.LOCALES}
        self.assertIsNone(b.merge(none, none)["koKR"])


class Discovery(unittest.TestCase):
    def test_extracts_only_forever_versions_newest_first(self):
        text = ('1.15.9.69722 {"version":"1.60.1.70205"} 1.60.1.69977 3.4.5.12345 '
                '11.2.0.61234 1.60.1.7020512 "1.60.2.70300"')
        self.assertEqual(b.extract_forever_versions(text), ["1.60.2.70300", "1.60.1.70205", "1.60.1.69977"])

    def test_blizzard_version_server_format(self):
        text = "Region!STRING:0|BuildId!DEC:4|VersionsName!String:0\nus|70205|1.60.1.70205\neu|70205|1.60.1.70205"
        self.assertEqual(b.extract_forever_versions(text), ["1.60.1.70205"])

    def test_discovery_failures_are_not_fatal(self):
        old = b._fetch_text
        b._fetch_text = lambda url, timeout=60: (_ for _ in ()).throw(OSError("нет сети"))
        try:
            self.assertEqual(b.discover_forever_builds(), [])
        finally:
            b._fetch_text = old

    def test_discovered_builds_go_first_then_static_fallback(self):
        old = b.discover_forever_builds
        b.discover_forever_builds = lambda: ["1.60.1.99999", "1.60.1.88888", "1.60.1.77777", "1.60.1.66666"]
        try:
            candidates = b.forever_build_candidates()
            self.assertEqual(candidates[:3], ["1.60.1.99999", "1.60.1.88888", "1.60.1.77777"])
            for fallback in b.FOREVER_BUILDS:
                self.assertIn(fallback, candidates)
        finally:
            b.discover_forever_builds = old


class Validate(unittest.TestCase):
    def merged(self, **overrides):
        en = {i: f"Item {i}" for i in range(1, 301)}
        data = {loc: {i: f"{loc} {i}" for i in range(1, 301)} for loc in b.LOCALES}
        data["enUS"] = en
        data.update(overrides)
        return data

    def test_ok(self):
        old = b.MIN_ITEMS; b.MIN_ITEMS = 100
        try:
            result = b.validate(self.merged())
            self.assertTrue(all(result[loc] for loc in b.LOCALES))
        finally:
            b.MIN_ITEMS = old

    def test_required_language_same_as_english_stops_build(self):
        old = b.MIN_ITEMS; b.MIN_ITEMS = 100
        try:
            same = {i: f"Item {i}" for i in range(1, 301)}
            with self.assertRaises(SystemExit):
                b.validate(self.merged(ruRU=same))
        finally:
            b.MIN_ITEMS = old

    def test_optional_language_problem_becomes_empty(self):
        old = b.MIN_ITEMS; b.MIN_ITEMS = 100
        try:
            result = b.validate(self.merged(koKR={1: "x"}, frFR=None))
            self.assertIsNone(result["koKR"])
            self.assertIsNone(result["frFR"])
            self.assertIsNotNone(result["deDE"])
        finally:
            b.MIN_ITEMS = old

    def test_too_few_english_names_stops_build(self):
        old = b.MIN_ITEMS; b.MIN_ITEMS = 100000
        try:
            with self.assertRaises(SystemExit):
                b.validate(self.merged())
        finally:
            b.MIN_ITEMS = old


class FullBuild(unittest.TestCase):
    def test_inspect_rows_prints_all_columns_and_matches(self):
        def table_download(build, locale, attempts=3, table="ItemSparse"):
            return "ID,AreaID,Corpse_0\n1,5,10.5\n2,36,20.5\n3,36,30.5\n"
        with build_workspace(table_download):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                b.inspect_rows(["Map"], ["AreaID=36", "Nope=1"])
        text = out.getvalue()
        self.assertIn("колонки: ID, AreaID, Corpse_0", text)
        self.assertIn("AreaID=36: найдено 2", text)
        self.assertIn("'Corpse_0': '30.5'", text)
        self.assertNotIn("Nope=1", text)       # колонки нет: условие пропущено

    def test_inspect_tables_prints_columns_and_samples(self):
        def table_download(build, locale, attempts=3, table="ItemSparse"):
            word = {"enUS": "Forest", "ruRU": "Лес"}[locale]
            return "ID,ContinentID,AreaName_lang\n1,0,%s 1\n2,0,%s 2\n" % (word, word)
        with build_workspace(table_download):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                b.inspect_tables()
        text = out.getvalue()
        self.assertIn("=== AreaTable", text)
        self.assertIn("=== Map", text)
        self.assertIn("колонки: ID, ContinentID, AreaName_lang", text)
        self.assertIn("AreaName_lang: отличается между enUS и ruRU в 2 строках", text)

    def test_generates_all_modules_and_build_marker(self):
        with build_workspace(fake_download(), interface="16002") as root:
            run_build()
            modules = sorted(os.listdir(os.path.join(root, "Modules")))
            self.assertEqual(modules, sorted(f"Polyglot_{loc}" for loc in b.LOCALES))
            with open(os.path.join(root, "Build.lua"), encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), 'PolyglotDataBuild = "1.60.1.70205"')
            toc = read(root, "Modules", "Polyglot_ruRU", "Polyglot_ruRU.toc")
            self.assertIn("## Interface: 16002", toc)        # берётся из основного Polyglot.toc
            self.assertIn("## LoadOnDemand: 1", toc)
            self.assertIn("## Dependencies: Polyglot", toc)
            self.assertIn("## Group: Polyglot", toc)          # группировка в списке аддонов
            self.assertIn("## Category: Localization", toc)   # категория на двух языках
            self.assertIn("## Category-ruRU: Локализация", toc)
            self.assertIn("## IconTexture: Interface\\Icons\\INV_Misc_Book_04", toc)   # иконка в списке аддонов

    def test_data_files_load_in_lua51_across_chunks(self):
        # 9000 предметов = 3 блока по 4000: проверяем, что разбиение на блоки не теряет записи
        with build_workspace(fake_download(count=9000)) as root:
            run_build()
            lua = lua51.LuaRuntime()
            for loc in ("enUS", "ruRU"):
                lua.execute(read(root, "Modules", f"Polyglot_{loc}", "Data.lua"))
            self.assertEqual(lua.eval('PolyglotData.ruRU.n[8999]'), "Предмет 8999")
            self.assertEqual(lua.eval('PolyglotData.ruRU.l[8999]'), "предмет 8999")
            self.assertEqual(lua.eval('(function() local c = 0 for _ in pairs(PolyglotData.enUS.n) do c = c + 1 end return c end)()'), 9000)

    def test_special_characters_are_escaped(self):
        names = {5: 'Quote " and \\ backslash', 6: "Ärmel и Ёлка"}
        with build_workspace(fake_download(overrides={"enUS": names})) as root:
            run_build()
            lua = lua51.LuaRuntime()
            lua.execute(read(root, "Modules", "Polyglot_enUS", "Data.lua"))
            self.assertEqual(lua.eval("PolyglotData.enUS.n[5]"), 'Quote " and \\ backslash')
            self.assertEqual(lua.eval("PolyglotData.enUS.l[6]"), "ärmel и ёлка")

    def _places(self, root, loc="ruRU"):
        lua = lua51.LuaRuntime()
        for l in ("enUS", loc):
            lua.execute(read(root, "Modules", f"Polyglot_{l}", "Data.lua"))
        return lua

    def test_places_zones_and_dungeons_written(self):
        with build_workspace(fake_download()) as root:
            run_build()
            lua = self._places(root)
            self.assertEqual(lua.eval("PolyglotData.ruRU.p[2]"), "Зона 2")
            self.assertEqual(lua.eval("PolyglotData.ruRU.pl[2]"), "зона 2")
            self.assertEqual(lua.eval("PolyglotData.enUS.k[2]"), "z")        # зона верхнего уровня
            self.assertEqual(lua.eval("PolyglotData.enUS.k[5]"), "s")        # подзона (есть родитель)
            self.assertEqual(lua.eval("PolyglotData.enUS.k[1000033]"), "d")  # подземелье из Map
            self.assertEqual(lua.eval("PolyglotData.enUS.k[1000409]"), "r")  # рейд
            self.assertEqual(lua.eval("PolyglotData.enUS.k[1000030]"), "b")  # поле боя
            self.assertEqual(lua.eval("PolyglotData.enUS.k[1000000]"), "c")  # континент
            self.assertEqual(lua.eval("PolyglotData.ruRU.p[1000033]"), "Подземелье 33")

    def test_place_maps_for_zones_subzones_continents_and_entrances(self):
        with build_workspace(fake_download()) as root:
            run_build()
            lua = self._places(root)
            self.assertEqual(lua.eval("PolyglotData.enUS.mp[2] == nil"), True)       # зона 2 без карты в данных
            self.assertEqual(lua.eval("PolyglotData.enUS.mp[1][1]"), 1426)           # зона 1
            self.assertEqual(lua.eval("#PolyglotData.enUS.mp[1]"), 1)                # без метки
            self.assertEqual(lua.eval("PolyglotData.enUS.mp[5][1]"), 1426)           # подзона берёт карту родителя
            self.assertEqual(lua.eval("PolyglotData.enUS.mp[1000000][1]"), 1415)     # континент
            # вход в подземелье: карта самой маленькой зоны, где он лежит, и доли карты
            self.assertEqual(lua.eval("PolyglotData.enUS.mp[1000033][1]"), 1426)
            self.assertAlmostEqual(lua.eval("PolyglotData.enUS.mp[1000033][2]"), 0.5486, places=3)
            self.assertAlmostEqual(lua.eval("PolyglotData.enUS.mp[1000033][3]"), 0.3420, places=3)
            self.assertIsNone(lua.eval("PolyglotData.enUS.mp[1000409]"))             # у рейда координат входа нет

    def test_missing_map_tables_do_not_break_places(self):
        base = fake_download()

        def no_uimap(build, locale, attempts=3, table="ItemSparse"):
            if table in ("UiMap", "UiMapAssignment"):
                raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
            return base(build, locale, table=table)
        with build_workspace(no_uimap) as root:
            out = run_build()
            self.assertIn("данные карт не получены", out)
            lua = self._places(root)
            self.assertEqual(lua.eval("PolyglotData.ruRU.p[2]"), "Зона 2")
            self.assertIsNone(lua.eval("next(PolyglotData.enUS.mp)"))

    def test_places_junk_and_duplicates_are_dropped(self):
        with build_workspace(fake_download()) as root:
            run_build()
            lua = self._places(root)
            self.assertIsNone(lua.eval("PolyglotData.enUS.p[8]"))           # служебная «Test Area»
            self.assertIsNone(lua.eval("PolyglotData.enUS.p[9]"))           # пустое название
            self.assertIsNone(lua.eval("PolyglotData.enUS.p[10]"))          # «Not Used ...» в середине названия
            self.assertIsNone(lua.eval("PolyglotData.enUS.p[11]"))          # пометка (Unused)
            self.assertIsNone(lua.eval("PolyglotData.enUS.p[7]"))           # дубль названия «Zone 6»
            self.assertIsNotNone(lua.eval("PolyglotData.enUS.p[6]"))
            self.assertIsNone(lua.eval("PolyglotData.enUS.p[1000013]"))     # карта из каталога test

    def test_places_failure_does_not_break_items(self):
        base = fake_download()

        def no_places(build, locale, attempts=3, table="ItemSparse"):
            if table != "ItemSparse":
                raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
            return base(build, locale)
        with build_workspace(no_places) as root:
            out = run_build()
            self.assertIn("поиск по местам работать не будет", out)
            lua = self._places(root)
            self.assertEqual(lua.eval("PolyglotData.ruRU.n[5]"), "Предмет 5")
            self.assertEqual(lua.eval("next(PolyglotData.enUS.p)"), None)

    def test_optional_language_missing_gives_empty_module(self):
        with build_workspace(fake_download(fail=("koKR",))) as root:
            run_build()
            data = read(root, "Modules", "Polyglot_koKR", "Data.lua")
            self.assertIn("empty = true", data)

    def test_required_language_failure_writes_nothing(self):
        with build_workspace(fake_download(fail=("ruRU",))) as root:
            with self.assertRaises(SystemExit):
                run_build()
            self.assertFalse(os.path.exists(os.path.join(root, "Modules")))
            self.assertFalse(os.path.exists(os.path.join(root, "Build.lua")))

    def test_check_mode_prints_only_the_build(self):
        import contextlib, io
        with build_workspace(fake_download()):
            out, err = io.StringIO(), io.StringIO()
            old_argv = helpers.sys.argv
            helpers.sys.argv = ["build_data.py", "--check"]
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    b.main()
            finally:
                helpers.sys.argv = old_argv
            self.assertEqual(out.getvalue().strip(), "1.60.1.70205")



def run_find(item_id):
    import contextlib, io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        b.find_item(item_id)
    return out.getvalue()


class FindItem(unittest.TestCase):
    @staticmethod
    def fake(build, locale, attempts=3, table="ItemSparse"):
        from helpers import csv_text
        rows = [(1, "Hearthstone")]
        if table == "ItemSearchName" and build == "1.60.1.70205":
            rows.append((7708, "Necrotic Wand" if locale == "enUS" else "Некротический жезл"))
        if table == "ItemSparse" and build.startswith("1.15"):
            rows.append((2721, "Holy Shroud"))
        return csv_text(rows)

    def test_item_only_in_search_table_is_reported_as_such(self):
        with build_workspace(self.fake):
            text = run_find(7708)
        self.assertIn("Forever 1.60.1.70205 | ItemSparse: enUS: нет записи", text)
        self.assertIn("Forever 1.60.1.70205 | ItemSearchName: enUS: «Necrotic Wand»", text)
        self.assertIn("ruRU: «Некротический жезл»", text)

    def test_item_only_in_classic_era(self):
        with build_workspace(self.fake):
            text = run_find(2721)
        self.assertIn("Classic Era 1.15.9.69722 | ItemSparse: enUS: «Holy Shroud»", text)

    def test_item_nowhere(self):
        with build_workspace(self.fake):
            text = run_find(999999)
        self.assertNotIn("«", text)
        self.assertIn("нет записи", text)


if __name__ == "__main__":
    unittest.main()
