import os
import shutil
import unittest

from helpers import Game, build_workspace, fake_download, run_build

NAMES = {
    "enUS": {1: "Thunderfury, Blessed Blade of the Windseeker", 2: "Hearthstone", 3: "Hearthstone Charm"},
    "ruRU": {1: "Громовая Ярость, благословенный клинок Искателя Ветра", 2: "Камень возвращения", 3: "Амулет камня"},
    "deDE": {1: "Donnerfury, Geschenk des Windsuchers", 2: "Ruhestein", 3: "Ärmel"},
}


class AddonTestCase(unittest.TestCase):
    def setUp(self):
        self.ctx = build_workspace(fake_download(count=300, overrides=NAMES))
        self.root = self.ctx.__enter__()
        run_build()
        self.modules = os.path.join(self.root, "Modules")

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def game(self, **kw):
        return Game(self.modules, **kw)


class Languages(AddonTestCase):
    def test_defaults_follow_client_locale(self):
        self.assertEqual(self.game(locale="ruRU").langs(), "ruRU")
        self.assertEqual(self.game(locale="deDE").langs(), "deDE")
        self.assertEqual(self.game(locale="enUS").langs(), "")
        self.assertEqual(self.game(locale="enGB").langs(), "")

    def test_old_saved_settings_survive_migration(self):
        g = self.game(locale="enGB", saved="{ strict = true }")
        self.assertTrue(g.ev("db.strict"))
        self.assertEqual(g.langs(), "")

    def test_nothing_loads_until_window_is_opened(self):
        g = self.game()
        self.assertEqual(g.loaded(), "")
        g.slash("")
        self.assertEqual(g.loaded(), "Polyglot_enUS,Polyglot_ruRU")

    def test_enabling_a_language_loads_only_that_module(self):
        g = self.game(); g.slash("")
        g.toggle_language("deDE", True)
        self.assertEqual(g.loaded(), "Polyglot_enUS,Polyglot_ruRU,Polyglot_deDE")
        self.assertEqual(g.langs(), "deDE,ruRU")

    def test_disabled_language_is_not_searched(self):
        g = self.game(); g.slash("")
        self.assertEqual(g.search("ruhestein")[0], 0)           # немецкий не включён
        g.toggle_language("deDE", True)
        self.assertEqual(g.search("ruhestein"), (1, ["2:1"]))
        g.toggle_language("deDE", False)
        self.assertEqual(g.search("ruhestein")[0], 0)

    def test_missing_module_is_reported_but_others_work(self):
        shutil.rmtree(os.path.join(self.modules, "Polyglot_frFR"))
        g = self.game(); g.slash("")
        g.toggle_language("frFR", True)
        self.assertIn("не найден", g.ev("statuses.frFR._text"))
        g.type_and_search("hearthstone")
        self.assertIn("frFR", g.ev("status._text"))
        self.assertGreater(g.search("hearthstone")[0], 0)

    def test_missing_english_module_shows_reinstall_hint(self):
        shutil.rmtree(os.path.join(self.modules, "Polyglot_enUS"))
        g = self.game(); g.slash("")
        g.type_and_search("item")
        self.assertIn("Polyglot_enUS", g.ev("status._text"))

    def test_empty_module_is_marked_as_no_data(self):
        with build_workspace(fake_download(count=300, overrides=NAMES, fail=("koKR",))) as root:
            run_build()
            g = Game(os.path.join(root, "Modules")); g.slash("")
            g.toggle_language("koKR", True)
            self.assertIn("нет данных", g.ev("statuses.koKR._text"))


class Searching(AddonTestCase):
    def setUp(self):
        super().setUp()
        self.g = self.game()
        self.g.slash("")
        self.g.toggle_language("deDE", True)

    def test_finds_by_any_enabled_language(self):
        self.assertEqual(self.g.search("thunderfury")[1][0], "1:2")
        self.assertEqual(self.g.search("громовая ярость")[1][0], "1:2")
        self.assertEqual(self.g.search("donnerfury")[1][0], "1:2")

    def test_case_insensitive_including_cyrillic_and_umlauts(self):
        self.assertEqual(self.g.search("КАМЕНЬ ВОЗВРАЩЕНИЯ", strict=True), (1, ["2:1"]))
        self.assertEqual(self.g.search("ÄRMEL", strict=True), (1, ["3:1"]))

    def test_extra_spaces_are_ignored(self):
        self.assertEqual(self.g.search("  камень   возвращения  ", strict=True), (1, ["2:1"]))

    def test_strict_requires_full_name(self):
        self.assertEqual(self.g.search("hearthstone", strict=True), (1, ["2:1"]))
        self.assertEqual(self.g.search("камень", strict=True)[0], 0)

    def test_non_strict_ranks_exact_then_prefix(self):
        count, items = self.g.search("hearthstone")
        self.assertEqual(items[:2], ["2:1", "3:2"])

    def test_words_in_any_order(self):
        self.assertEqual(self.g.search("ветра искателя"), (1, ["1:4"]))
        self.assertEqual(self.g.search("ветра искателя", strict=True)[0], 0)


class Pagination(AddonTestCase):
    def setUp(self):
        super().setUp()
        self.g = self.game(locale="enUS")
        self.g.slash("")
        self.g.type_and_search("item")          # результатов заметно больше одной страницы

    def numbers(self):
        """(текущая страница, всего страниц) из подписи на любом языке."""
        import re
        found = re.findall(r"\d+", self.g.ev("pageText._text"))
        return int(found[0]), int(found[1])

    def click(self, button, shift=False):
        self.g.lua.execute(f"SHIFT = {str(shift).lower()}; {button}._scripts.OnClick(); SHIFT = false")

    def test_next_and_previous(self):
        self.assertEqual(self.numbers()[0], 1)
        self.assertFalse(self.g.ev("prevBtn._enabled"))
        self.click("nextBtn")
        self.assertEqual(self.numbers()[0], 2)
        self.assertTrue(self.g.ev("prevBtn._enabled"))
        self.click("prevBtn")
        self.assertEqual(self.numbers()[0], 1)

    def test_shift_click_jumps_to_edges_and_last_page_disables_next(self):
        pages = self.numbers()[1]
        self.click("nextBtn", shift=True)
        self.assertEqual(self.numbers(), (pages, pages))
        self.assertFalse(self.g.ev("nextBtn._enabled"))
        self.click("nextBtn")                                   # дальше последней не уходит
        self.assertEqual(self.numbers(), (pages, pages))
        self.click("prevBtn", shift=True)
        self.assertEqual(self.numbers()[0], 1)

    def test_mouse_wheel(self):
        self.g.lua.execute("frame._scripts.OnMouseWheel(frame, -1)")
        self.assertEqual(self.numbers()[0], 2)
        self.g.lua.execute("frame._scripts.OnMouseWheel(frame, 1)")
        self.assertEqual(self.numbers()[0], 1)

    def test_new_search_returns_to_first_page(self):
        self.click("nextBtn")
        self.g.type_and_search("item 1")
        self.assertEqual(self.numbers()[0], 1)

    def test_rows_per_page_shrink_with_more_languages(self):
        english_only = self.g.ev("perPage")
        self.g.lua.execute("db.langs = {ruRU=true, deDE=true, esES=true, itIT=true, ptBR=true, esMX=true, "
                           "koKR=true, zhCN=true, zhTW=true, frFR=true}; DoSearch()")
        self.assertLess(self.g.ev("perPage"), english_only)
        self.assertGreaterEqual(self.g.ev("perPage"), 1)


class ClientBuildNotice(AddonTestCase):
    def test_silent_when_builds_match(self):
        g = self.game(client_build="70205", data_build="1.60.1.70205", saved="{ seenWelcome = true }")
        g.fire("PLAYER_LOGIN")
        self.assertEqual(g.printed(), [])

    def test_notice_when_client_updated_and_only_once(self):
        g = self.game(client_build="70300", data_build="1.60.1.70205", saved="{ seenWelcome = true }")
        g.fire("PLAYER_LOGIN")
        self.assertEqual(len(g.printed()), 1)
        self.assertIn("70300", g.printed()[0]); self.assertIn("70205", g.printed()[0])
        g.fire("PLAYER_LOGIN")
        self.assertEqual(len(g.printed()), 1)

    def test_notice_when_build_marker_missing(self):
        g = self.game(data_build=None, saved="{ seenWelcome = true }")
        g.fire("PLAYER_LOGIN")
        self.assertIn("метки сборки", g.printed()[0])

    def test_info_command(self):
        g = self.game(); g.slash("info")
        text = "\n".join(g.printed())
        self.assertIn("Interface 16001", text); self.assertIn("enUS: 300 названий", text); self.assertIn("ruRU: 300 названий", text)


class Settings(AddonTestCase):
    def test_options_panel_registered_and_openable(self):
        g = self.game()
        self.assertTrue(g.ev("REGISTERED ~= nil"))
        g.slash("lang")
        self.assertEqual(g.ev("OPENED"), 42)



class Favorites(AddonTestCase):
    def setUp(self):
        super().setUp()
        self.g = self.game(locale="enUS")
        self.g.slash("")
        self.g.type_and_search("hearthstone")        # id 2 (точное) и id 3

    def star(self, i):
        self.g.lua.execute(f"rows[{i}].star._scripts.OnClick()")

    def favorites(self):
        return self.g.ev("(function() local o = {} for k in pairs(db.favorites) do o[#o+1] = k end "
                         "table.sort(o) return table.concat(o, ',') end)()")

    def test_star_toggles_favorite_and_updates_counter(self):
        self.assertEqual(self.favorites(), "")
        self.star(1)
        self.assertEqual(self.favorites(), "2")
        self.assertIn("(1)", self.g.ev("favBtn._text"))
        self.assertEqual(self.g.ev("rows[1].star.tex._alpha"), 1)
        self.star(1)
        self.assertEqual(self.favorites(), "")
        self.assertIn("(0)", self.g.ev("favBtn._text"))

    def test_favorites_view_lists_only_marked_items(self):
        self.star(2)                                   # id 3
        self.g.lua.execute("favBtn._scripts.OnClick()")
        self.assertTrue(self.g.ev("viewFav"))
        self.assertEqual(self.g.ev("#currentResults_ids()") if False else self.g.ev(
            "(function() local o = {} for i = 1, #rows do if rows[i]._shown then o[#o+1] = rows[i].id end end return table.concat(o, ',') end)()"), "3")
        self.g.lua.execute("favBtn._scripts.OnClick()")  # назад к поиску
        self.assertFalse(self.g.ev("viewFav"))

    def test_query_filters_the_favorites_list(self):
        self.star(1); self.star(2)
        self.g.lua.execute("favBtn._scripts.OnClick()")
        self.g.type_and_search("charm")
        shown = self.g.ev("(function() local o = {} for i = 1, #rows do if rows[i]._shown then o[#o+1] = rows[i].id end end return table.concat(o, ',') end)()")
        self.assertEqual(shown, "3")

    def test_removing_from_favorites_view_updates_the_list(self):
        self.star(1)
        self.g.lua.execute("favBtn._scripts.OnClick()")
        self.star(1)
        self.assertEqual(self.favorites(), "")
        self.assertIn("No favorites", self.g.ev("status._text"))

    def test_favorites_are_saved_in_settings_and_survive_reload(self):
        self.star(1)
        saved = self.g.ev("(function() local o = {} for k in pairs(PolyglotDB.favorites) do o[#o+1] = k end return table.concat(o, ',') end)()")
        self.assertEqual(saved, "2")
        g2 = self.game(locale="enUS", saved="{ favorites = { [3] = true }, seenWelcome = true }")
        self.assertIn("(1)", g2.ev("favBtn._text"))


class MinimapButton(AddonTestCase):
    def setUp(self):
        super().setUp()
        self.g = self.game(locale="enUS")

    def test_left_click_toggles_window_right_click_opens_settings(self):
        self.assertFalse(self.g.ev("frame._shown"))
        self.g.lua.execute('minimapBtn._scripts.OnClick(minimapBtn, "LeftButton")')
        self.assertTrue(self.g.ev("frame._shown"))
        self.g.lua.execute('minimapBtn._scripts.OnClick(minimapBtn, "LeftButton")')
        self.assertFalse(self.g.ev("frame._shown"))
        self.g.lua.execute('minimapBtn._scripts.OnClick(minimapBtn, "RightButton")')
        self.assertEqual(self.g.ev("OPENED"), 42)

    def test_dragging_saves_angle_around_the_minimap(self):
        # курсор прямо над центром миникарты -> 90 градусов
        self.g.lua.execute("minimapBtn._scripts.OnDragStart(minimapBtn); minimapBtn._scripts.OnUpdate(minimapBtn)")
        self.assertAlmostEqual(self.g.ev("db.minimapAngle"), 90, places=3)
        self.g.lua.execute("minimapBtn._scripts.OnDragStop(minimapBtn)")
        self.assertIsNone(self.g.ev("minimapBtn._scripts.OnUpdate"))

    def test_command_hides_and_shows_button_and_is_remembered(self):
        self.g.slash("minimap")
        self.assertTrue(self.g.ev("db.hideMinimap"))
        self.assertFalse(self.g.ev("minimapBtn._shown"))
        self.g.slash("minimap")
        self.assertIsNone(self.g.ev("db.hideMinimap"))
        self.assertTrue(self.g.ev("minimapBtn._shown"))

    def test_hidden_state_is_restored_after_reload(self):
        g2 = self.game(locale="enUS", saved="{ hideMinimap = true, seenWelcome = true }")
        self.assertFalse(g2.ev("minimapBtn._shown"))

    def test_checkbox_in_settings_hides_button(self):
        self.g.lua.execute("minimapCheck = nil")   # (значение ниже берётся из панели)
        self.g.lua.execute("for _, f in ipairs(FRAMES) do end")
        self.assertIsNotNone(self.g.ev("COMPARTMENT"))   # пункт меню аддонов зарегистрирован
        self.assertEqual(self.g.ev("COMPARTMENT.text"), "Polyglot")


class InterfaceLanguage(AddonTestCase):
    def test_russian_client_gets_russian_interface(self):
        g = self.game(locale="ruRU"); g.slash("")
        g.type_and_search("item 7")
        self.assertIn("Найдено", g.ev("status._text"))
        self.assertIn("Избранное", g.ev("favBtn._text"))

    def test_other_clients_get_english(self):
        for loc in ("enUS", "deDE", "frFR"):
            g = self.game(locale=loc); g.slash("")
            g.type_and_search("item 7")
            self.assertIn("Found", g.ev("status._text"), loc)
            self.assertIn("Favorites", g.ev("favBtn._text"), loc)

    def test_both_languages_have_the_same_keys(self):
        import re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Polyglot.lua"), encoding="utf-8").read()
        block_en = src[src.index("    enUS = {"):src.index("    ruRU = {")]
        block_ru = src[src.index("    ruRU = {"):src.index("local CURRENT")]
        keys = lambda b: set(re.findall(r'\b([a-z][a-z_0-9]*) = "', b))
        self.assertEqual(keys(block_en) ^ keys(block_ru), set())

    def test_every_translation_key_used_in_code_exists(self):
        import re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Polyglot.lua"), encoding="utf-8").read()
        used = {k for k in re.findall(r'T\("(\w+)"', src) if not k.endswith("_")}   # help_N формируются в цикле
        block_en = src[src.index("    enUS = {"):src.index("    ruRU = {")]
        defined = set(re.findall(r'\b([a-z][a-z_0-9]*) = "', block_en))
        self.assertEqual(used - defined, set())
        for i in range(1, 8):
            self.assertIn(f"help_{i}", defined)


class HelpAndWelcome(AddonTestCase):
    def test_help_lists_commands(self):
        g = self.game(locale="enUS"); g.slash("help")
        text = "\n".join(g.printed())
        for command in ("/pg lang", "/pg minimap", "/pg info"):
            self.assertIn(command, text)

    def test_welcome_is_shown_once(self):
        g = self.game(locale="ruRU")
        g.fire("PLAYER_LOGIN")
        self.assertTrue(any("/pg" in line for line in g.printed()))
        self.assertTrue(g.ev("PolyglotDB.seenWelcome"))
        before = len(g.printed())
        g.fire("PLAYER_LOGIN")
        self.assertEqual(len(g.printed()), before)


class Places(AddonTestCase):
    """Вкладка «Места»: зоны и подземелья."""

    def shown(self, g):
        return g.ev("(function() local n = 0 for i = 1, #rows do if rows[i]._shown then n = n + 1 end end return n end)()")

    def test_tab_switch_searches_places_not_items(self):
        g = self.game(locale="ruRU", saved="{ langs = {ruRU = true}, seenWelcome = true }")
        g.slash("")
        g.lua.execute('edit._text = "зона 2"')
        g.lua.execute("tabPlacesBtn._scripts.OnClick()")
        self.assertEqual(g.ev("tab"), "places")
        self.assertEqual(self.shown(g), 1)
        text = g.ev("rows[1].text._text")
        self.assertIn("Zone 2", text)
        self.assertIn("Зона 2", text)
        self.assertIn("зона", text)            # вид места на языке интерфейса

    def test_place_title_uses_interface_language(self):
        g = self.game(locale="ruRU", saved="{ langs = {ruRU = true}, seenWelcome = true }")
        g.slash("zone зона 2")
        first = g.ev("rows[1].text._text").split("\n")[0]
        self.assertIn("Зона 2", first)
        g.lua.execute("uiBtn._scripts.OnClick()")     # интерфейс на английский
        first = g.ev("rows[1].text._text").split("\n")[0]
        self.assertIn("Zone 2", first)

    def test_place_search_by_other_language_and_loose_mode(self):
        g = self.game(locale="ruRU", saved="{ langs = {ruRU = true}, seenWelcome = true }")
        g.slash("zone подземелье 33")
        self.assertEqual(g.ev("tab"), "places")
        self.assertIn("Dungeon 33", g.ev("rows[1].text._text"))
        self.assertIn("подземелье", g.ev("rows[1].text._text"))

    def test_items_still_found_after_switching_back(self):
        g = self.game(locale="ruRU", saved="{ langs = {ruRU = true}, seenWelcome = true }")
        g.slash("zone зона")
        g.slash("Предмет 5")
        self.assertEqual(g.ev("tab"), "items")
        self.assertIn("5", g.ev("rows[1].text._text"))

    def test_place_ids_do_not_leak_into_item_search(self):
        g = self.game(locale="enUS")
        count, _ = g.search("zone 2")
        self.assertEqual(count, 0)

    def test_old_data_without_places_shows_message(self):
        g = self.game(locale="enUS")
        g.slash("Item 5")   # загружает модули
        g.lua.execute("PolyglotData.enUS.p = nil")
        g.slash("zone anything")
        self.assertIn("No place data", g.ev("status._text"))

    def test_favorites_button_hidden_on_places(self):
        g = self.game(locale="enUS")
        g.slash("zone zone")
        self.assertFalse(g.ev("favBtn._shown"))
        g.slash("Item 5")
        self.assertTrue(g.ev("favBtn._shown"))


class ItemTooltip(AddonTestCase):
    """Названия предмета на других языках в обычной подсказке."""

    LANGS = "{ langs = {ruRU = true, deDE = true}, seenWelcome = true%s }"

    def tooltip(self, g, item_id=5, owner=None):
        g.lua.execute("tt = NewTT(%s)" % ("nil" if owner is None else owner))
        g.lua.execute(f"TT_CB(tt, {{ id = {item_id} }})")
        return [g.lua.eval(f"tt.lines[{i}]") for i in range(1, int(g.lua.eval("#tt.lines")) + 1)]

    def test_default_mode_alt_shows_lines_only_with_alt(self):
        g = self.game(locale="ruRU", saved=self.LANGS % "")
        self.assertEqual(self.tooltip(g), [])                      # Alt не зажат
        g.lua.execute("ALT = true")
        lines = self.tooltip(g)
        self.assertEqual(len(lines), 2)                            # en и de, язык клиента (ru) пропущен
        self.assertIn("enUS", lines[0]); self.assertIn("Item 5", lines[0])
        self.assertIn("deDE", lines[1]); self.assertIn("Gegenstand 5", lines[1])

    def test_lines_mode_one_line_per_language(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'")
        lines = self.tooltip(g)
        self.assertEqual(len(lines), 2)
        self.assertFalse(any("ruRU" in l for l in lines))          # язык клиента не дублируется

    def test_one_mode_gives_single_line_english_first(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'one'")
        lines = self.tooltip(g)
        self.assertEqual(len(lines), 1)
        self.assertIn("enUS", lines[0])

    def test_off_mode_adds_nothing_even_with_alt(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'off'")
        g.lua.execute("ALT = true")
        self.assertEqual(self.tooltip(g), [])

    def test_english_client_skips_english_line(self):
        g = self.game(locale="enUS", saved=self.LANGS % ", tooltip = 'lines'")
        lines = self.tooltip(g)
        self.assertFalse(any("enUS" in l for l in lines))
        self.assertTrue(any("deDE" in l for l in lines))

    def test_line_equal_to_displayed_title_is_skipped(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'")
        g.lua.execute("TT_TITLE = 'Item 5'")
        lines = self.tooltip(g)
        self.assertEqual(len(lines), 1)
        self.assertIn("deDE", lines[0])

    def test_item_unknown_to_database_adds_nothing(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'")
        self.assertEqual(self.tooltip(g, item_id=999999), [])

    def test_tooltip_of_own_result_row_is_not_duplicated(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'")
        g.slash("Предмет 5")
        self.assertEqual(self.tooltip(g, owner="rows[1]"), [])

    def test_tooltip_size_is_recalculated(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'")
        self.tooltip(g)
        self.assertEqual(g.ev("tt.shows"), 1)

    def test_alt_press_redraws_open_tooltip(self):
        g = self.game(locale="ruRU", saved=self.LANGS % "")
        g.lua.execute("REDRAWN = 0; local owner = {GetScript = function() return function() REDRAWN = REDRAWN + 1 end end}\n"
                      "GameTooltip.GetOwner = function() return owner end; GameTooltip._shown = true")
        g.fire("MODIFIER_STATE_CHANGED", '"LALT"')
        self.assertEqual(g.ev("REDRAWN"), 1)
        g.fire("MODIFIER_STATE_CHANGED", '"LSHIFT"')              # другие клавиши не в счёт
        self.assertEqual(g.ev("REDRAWN"), 1)

    def test_alt_press_ignored_in_other_modes(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'")
        g.lua.execute("REDRAWN = 0; local owner = {GetScript = function() return function() REDRAWN = REDRAWN + 1 end end}\n"
                      "GameTooltip.GetOwner = function() return owner end; GameTooltip._shown = true")
        g.fire("MODIFIER_STATE_CHANGED", '"LALT"')
        self.assertEqual(g.ev("REDRAWN"), 0)

    def test_legacy_hook_path_works_without_tooltip_data_processor(self):
        g = self.game(locale="ruRU", saved=self.LANGS % ", tooltip = 'lines'", legacy_tooltip=True)
        g.lua.execute("LEGACY_LINK = '|cffffffff|Hitem:5::::|h[x]|h|r'; LEGACY_HOOK(GameTooltip)")
        self.assertEqual(g.ev("#LEGACY_LINES"), 2)


class TooltipSettings(AddonTestCase):
    def test_four_radio_buttons_switch_mode(self):
        g = self.game(locale="enUS")
        modes = g.lua.eval("(function() local k = {} for m in pairs(ttChecks) do k[#k + 1] = m end table.sort(k) return table.concat(k, ',') end)()")
        self.assertEqual(modes.split(","), ["alt", "lines", "off", "one"])
        g.lua.execute("panel._scripts.OnShow(panel)")
        self.assertTrue(g.ev("ttChecks.alt._checked"))              # по умолчанию Alt
        g.lua.execute("ttChecks.lines._scripts.OnClick(ttChecks.lines)")
        self.assertEqual(g.ev("PolyglotDB.tooltip"), "lines")
        self.assertTrue(g.ev("ttChecks.lines._checked"))
        self.assertFalse(g.ev("ttChecks.alt._checked"))
        g.lua.execute("ttChecks.off._scripts.OnClick(ttChecks.off)")
        self.assertEqual(g.ev("PolyglotDB.tooltip"), "off")
        self.assertFalse(g.ev("ttChecks.lines._checked"))

    def test_unknown_saved_value_falls_back_to_alt(self):
        g = self.game(locale="enUS", saved="{ tooltip = 'nonsense', seenWelcome = true }")
        g.lua.execute("panel._scripts.OnShow(panel)")
        self.assertTrue(g.ev("ttChecks.alt._checked"))


class UiLanguageSetting(AddonTestCase):
    """Единый выбор языка интерфейса: в настройках и в окне поиска."""

    def click(self, g, key):
        g.lua.execute(f'uiChecks["{key}"]._scripts.OnClick(uiChecks["{key}"])')

    def test_settings_radio_switches_whole_addon_to_english(self):
        g = self.game(locale="ruRU")
        self.click(g, "enUS")
        self.assertEqual(g.ev("PolyglotDB.uiLang"), "enUS")
        self.assertEqual(g.ev("tabPlacesBtn._text"), "Places")                      # окно поиска
        self.assertEqual(g.ev("minimapLabel._text"), "Show the minimap button")      # настройки
        self.assertEqual(g.ev("uiBtn._text"), "EN")                                  # кнопка в окне
        self.assertEqual(g.ev("uiHeader._text"), "Addon interface language (window and settings)")
        self.assertEqual(g.ev("ttHeader._text"), "Item tooltips: names in your other languages")
        self.assertEqual(g.ev("ttLabels.off._text"), "Off")
        self.assertEqual(g.ev("uiLabels.auto._text"), "Auto (as in game)")
        self.assertTrue(g.ev("uiChecks.enUS._checked"))
        self.assertFalse(g.ev("uiChecks.auto._checked"))

    def test_back_to_auto_follows_client_language(self):
        g = self.game(locale="ruRU", saved="{ uiLang = 'enUS', seenWelcome = true }")
        self.click(g, "auto")
        self.assertTrue(g.ev("rawget(PolyglotDB, 'uiLang') == nil"))
        self.assertEqual(g.ev("tabPlacesBtn._text"), "Места")
        self.assertEqual(g.ev("uiBtn._text"), "RU")
        self.assertTrue(g.ev("uiChecks.auto._checked"))
        self.assertIn("Авто", g.ev("uiLabels.auto._text"))

    def test_window_button_updates_settings_radio(self):
        g = self.game(locale="ruRU")
        g.lua.execute("panel._scripts.OnShow(panel)")
        self.assertTrue(g.ev("uiChecks.auto._checked"))
        g.lua.execute("uiBtn._scripts.OnClick()")
        self.assertTrue(g.ev("uiChecks.enUS._checked"))
        self.assertFalse(g.ev("uiChecks.auto._checked"))
        self.assertEqual(g.ev("minimapLabel._text"), "Show the minimap button")

    def test_opening_settings_reapplies_all_texts(self):
        g = self.game(locale="ruRU", saved="{ uiLang = 'enUS', seenWelcome = true }")
        g.lua.execute("minimapLabel._text = 'stale text'")
        g.lua.execute("panel._scripts.OnShow(panel)")
        self.assertEqual(g.ev("minimapLabel._text"), "Show the minimap button")


class PlaceMap(AddonTestCase):
    """Клик по месту открывает карту; для подземелья ставится метка входа."""

    def click_first(self, g, query):
        g.slash("zone " + query)
        g.lua.execute("rows[1]._scripts.OnClick(rows[1])")

    def test_zone_click_opens_zone_map_without_pin(self):
        g = self.game(locale="enUS")
        self.click_first(g, "zone 1")
        self.assertEqual(g.ev("MAP_SET"), 1426)
        self.assertTrue(g.ev("MAP_SHOWN"))
        self.assertIsNone(g.ev("mapPin"))

    def test_subzone_opens_parent_zone_map(self):
        g = self.game(locale="enUS")
        self.click_first(g, "zone 5")
        self.assertEqual(g.ev("MAP_SET"), 1426)

    def test_dungeon_click_opens_map_and_shows_entrance_pin(self):
        g = self.game(locale="enUS")
        self.click_first(g, "dungeon 33")
        self.assertEqual(g.ev("MAP_SET"), 1426)
        self.assertTrue(g.ev("mapPin._shown"))
        self.assertAlmostEqual(g.ev("mapPin.want.x"), 0.5486, places=3)
        self.assertAlmostEqual(g.ev("mapPin.want.y"), 0.3420, places=3)

    def test_search_window_hides_when_map_opens(self):
        g = self.game(locale="enUS")
        self.click_first(g, "zone 1")
        self.assertFalse(g.ev("frame._shown"))

    def test_pin_is_big_and_removed_by_click(self):
        g = self.game(locale="enUS")
        self.click_first(g, "dungeon 33")
        self.assertTrue(g.ev("mapPin._shown"))
        g.lua.execute("mapPin._scripts.OnMouseUp(mapPin)")
        self.assertFalse(g.ev("mapPin._shown"))
        self.assertTrue(g.ev("rawget(mapPin, 'want') == nil"))
        g.lua.execute("HOOKS.OnMapChanged()")        # после снятия метка не возвращается
        self.assertFalse(g.ev("mapPin._shown"))

    def test_pin_removed_when_map_closes(self):
        g = self.game(locale="enUS")
        self.click_first(g, "dungeon 33")
        g.lua.execute("MAP_SCRIPTS.OnHide()")
        self.assertFalse(g.ev("mapPin._shown"))
        self.assertTrue(g.ev("rawget(mapPin, 'want') == nil"))

    def test_pin_hides_when_another_map_is_opened(self):
        g = self.game(locale="enUS")
        self.click_first(g, "dungeon 33")
        g.lua.execute("MAP_SET = 1415; HOOKS.OnMapChanged()")
        self.assertFalse(g.ev("mapPin._shown"))

    def test_pin_removed_when_opening_place_without_entrance(self):
        g = self.game(locale="enUS")
        self.click_first(g, "dungeon 33")
        self.click_first(g, "zone 1")
        self.assertFalse(g.ev("mapPin._shown"))

    def test_place_without_map_says_so(self):
        g = self.game(locale="enUS")
        self.click_first(g, "zone 2")
        self.assertIsNone(g.ev("MAP_SET"))
        self.assertTrue(any("No map data" in line for line in g.printed()))

    def test_item_click_does_not_open_map(self):
        g = self.game(locale="enUS")
        g.slash("Item 5")
        g.lua.execute("rows[1]._scripts.OnClick(rows[1])")
        self.assertIsNone(g.ev("MAP_SET"))

    def test_no_map_frame_does_not_crash(self):
        g = self.game(locale="enUS")
        g.lua.execute("WorldMapFrame = nil")
        self.click_first(g, "zone 1")
        self.assertTrue(any("No map data" in line for line in g.printed()))


class DisabledModule(AddonTestCase):
    def test_disabled_language_module_is_enabled_and_loaded(self):
        g = self.game(locale="enUS", saved="{ langs = {deDE = true}, seenWelcome = true }")
        g.lua.execute('DISABLED["Polyglot_deDE"] = true')
        g.slash("Item 5")
        self.assertIn("Polyglot_deDE", g.loaded())
        self.assertIsNone(g.ev("DISABLED['Polyglot_deDE']"))


class RowLines(AddonTestCase):
    """Каждая строка текста результата выводится отдельным FontString (многострочный текст клиент обрезает)."""

    def test_place_rows_have_one_line_per_language(self):
        g = self.game(locale="ruRU", saved="{ langs = {ruRU = true}, seenWelcome = true }")
        g.slash("zone зона 2")
        self.assertIn("Зона 2", g.ev("rows[1].lines[1]._text"))        # заголовок на языке интерфейса
        self.assertIn("Zone 2", g.ev("rows[1].lines[2]._text"))
        self.assertIn("Зона 2", g.ev("rows[1].lines[3]._text"))
        self.assertEqual(g.ev("rows[1].lines[4]._text"), "")

    def test_item_rows_have_one_line_per_language(self):
        g = self.game(locale="ruRU", saved="{ langs = {ruRU = true}, seenWelcome = true }")
        g.slash("Предмет 5")
        lines = [g.ev(f"rows[1].lines[{i}]._text") for i in range(1, 6)]
        self.assertTrue(any("enUS:|r Item 5" in l for l in lines), lines)
        self.assertTrue(any("ruRU:|r Предмет 5" in l for l in lines), lines)

    def test_disabled_module_hint(self):
        g = self.game(locale="ruRU", saved="{ langs = {itIT = true}, seenWelcome = true }")
        g.lua.execute('DISABLED["Polyglot_itIT"] = true; C_AddOns.EnableAddOn = nil; EnableAddOn = nil')
        g.slash("Предмет 5")
        g.lua.execute("panel._scripts.OnShow(panel)")
        self.assertIn("выключен в списке аддонов", g.ev("statuses.itIT._text"))


class Rename(AddonTestCase):
    """Переименование ItemLoc -> Polyglot: настройки, предупреждение, команды."""

    def test_settings_migrate_from_old_addon(self):
        g = self.game(locale="enUS", old_saved='{ strict = true, favorites = { [7708] = true }, seenWelcome = true }')
        self.assertTrue(g.ev("PolyglotDB.strict"))
        self.assertTrue(g.ev("PolyglotDB.favorites[7708]"))
        g.fire("PLAYER_LOGIN")
        self.assertTrue(any("ItemLoc" in line for line in g.printed()))

    def test_migration_is_a_copy(self):
        g = self.game(locale="enUS", old_saved="{ favorites = { [1] = true } }")
        g.lua.execute("PolyglotDB.favorites[2] = true")
        self.assertIsNone(g.ev("ItemLocDB.favorites[2]"))

    def test_no_migration_when_new_settings_exist(self):
        g = self.game(locale="enUS", saved="{ strict = false }", old_saved="{ strict = true }")
        self.assertFalse(g.ev("PolyglotDB.strict"))

    def test_old_addon_warning(self):
        g = self.game(locale="enUS", old_addon=True)
        g.fire("PLAYER_LOGIN")
        self.assertTrue(any("ItemLoc_*" in line for line in g.printed()))
        g2 = self.game(locale="enUS")
        g2.fire("PLAYER_LOGIN")
        self.assertFalse(any("ItemLoc" in line for line in g2.printed()))

    def test_slash_commands(self):
        g = self.game(locale="enUS")
        for name, expected in (("SLASH_POLYGLOT1", "/pg"), ("SLASH_POLYGLOT2", "/polyglot"), ("SLASH_POLYGLOT3", "/il")):
            self.assertEqual(g.ev(name), expected)


class KeyBinding(AddonTestCase):
    def test_binding_function_and_label_exist(self):
        g = self.game(locale="enUS")
        self.assertEqual(g.ev("BINDING_HEADER_POLYGLOT"), "Polyglot")
        self.assertTrue(g.ev("type(Polyglot_Toggle) == 'function'"))
        g.lua.execute("Polyglot_Toggle()")
        self.assertTrue(g.ev("frame._shown"))
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Bindings.xml"), encoding="utf-8") as f:
            xml = f.read()
        self.assertIn('name="POLYGLOT_TOGGLE"', xml)
        self.assertIn("Polyglot_Toggle()", xml)



class UiLanguageButton(AddonTestCase):
    def test_button_shows_current_language_and_switches(self):
        g = self.game(locale="ruRU"); g.slash("")
        self.assertEqual(g.ev("uiBtn._text"), "RU")
        self.assertIn("Избранное", g.ev("favBtn._text"))
        g.lua.execute("uiBtn._scripts.OnClick()")
        self.assertEqual(g.ev("uiBtn._text"), "EN")
        self.assertIn("Favorites", g.ev("favBtn._text"))
        self.assertEqual(g.ev("tabPlacesBtn._text"), "Places")
        g.lua.execute("uiBtn._scripts.OnClick()")
        self.assertEqual(g.ev("uiBtn._text"), "RU")

    def test_choice_is_saved_and_survives_reload(self):
        g = self.game(locale="ruRU")
        g.lua.execute("uiBtn._scripts.OnClick()")
        self.assertEqual(g.ev("PolyglotDB.uiLang"), "enUS")
        g2 = self.game(locale="ruRU", saved="{ uiLang = 'enUS', seenWelcome = true }")
        self.assertEqual(g2.ev("uiBtn._text"), "EN")
        self.assertIn("Favorites", g2.ev("favBtn._text"))

    def test_switch_updates_existing_results_and_settings_panel(self):
        g = self.game(locale="ruRU"); g.slash("")
        g.type_and_search("item 7")
        self.assertIn("Найдено", g.ev("status._text"))
        g.lua.execute("uiBtn._scripts.OnClick()")
        self.assertIn("Found", g.ev("status._text"))
        self.assertIn("Languages", g.ev("langBtn._text"))
        self.assertEqual(g.ev("minimapLabel._text"), "Show the minimap button")

    def test_empty_state_message_is_retranslated(self):
        g = self.game(locale="ruRU"); g.slash("")
        g.lua.execute('edit._text = ""; DoSearch()')
        self.assertIn("Введите", g.ev("status._text"))
        g.lua.execute("uiBtn._scripts.OnClick()")
        self.assertIn("Enter an item name", g.ev("status._text"))

    def test_slash_command_sets_and_resets_language(self):
        g = self.game(locale="ruRU")
        g.slash("ui en");   self.assertEqual(g.ev("uiBtn._text"), "EN")
        g.slash("ui ru");   self.assertEqual(g.ev("uiBtn._text"), "RU")
        g.slash("ui en");   g.slash("ui auto")
        self.assertEqual(g.ev("uiBtn._text"), "RU")             # вернулся язык клиента (ruRU)
        self.assertIsNone(g.ev("PolyglotDB.uiLang"))

    def test_auto_follows_client_language_for_other_locales(self):
        g = self.game(locale="deDE")
        self.assertEqual(g.ev("uiBtn._text"), "EN")


if __name__ == "__main__":
    unittest.main()
