from rpg_tabletop.ui.theme import palette, stylesheets


def test_stylesheets_resolve_every_color():
    for sheet in (
        stylesheets.CAMPAIGN_MANAGER_STYLESHEET,
        stylesheets.GM_RUNTIME_STYLESHEET,
        stylesheets.PLAYER_STYLESHEET,
    ):
        assert "$" not in sheet
        assert palette.COLORS["accent"] in sheet


def test_field_color():
    assert palette.field_color("STR") == palette.STAT_PALETTE["str"]
    assert palette.field_color("hp") == palette.RESOURCE_PALETTE["hp"]
    assert palette.field_color("unknown") == palette.DEFAULT_FIELD_COLOR
